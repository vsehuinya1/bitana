"""
Bitana Main Orchestrator

Startup sequence:
1. Load config → log checksum + version
2. Init DB → recover state
3. Init executor (paper or live)
4. Init watchdog → start supervised tasks
5. Enter main loop

Graceful shutdown on SIGINT/SIGTERM.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).parent))

from config.loader import load_config, resolve_symbol_config, AppConfig
from core.events import event_bus, Events
from core.health import HealthServer
from core.logging_setup import setup_logging, get_logger, TradeLogger
from core.models import (
    AlertTier, BrakeState, Candle, EngineType, Position, PositionState, RiskState, Side, Signal,
)
from core.watchdog import Watchdog
from data.force_order_pipeline import ForceOrderPipeline
from data.binance_rest import BinanceRestClient
from data.binance_ws import BinanceWebSocket
from data.candle_manager import CandleManager
from data.rate_limiter import RateLimiterGroup
from data.symbol_info import SymbolInfoManager
from engines.compression_breakout import CompressionBreakoutEngine
from engines.btc_regime import compute_btc_regime
from engines.liq_burst_follow_engine import BurstFollowState, LiqBurstFollowEngine
from engines.regime_filter import RegimeFilter
from engines.squeeze_engine import SqueezeEngine
from execution.base_executor import BaseExecutor
from execution.live_executor import LiveExecutor
from execution.order_manager import OrderManager
from execution.paper_executor import PaperExecutor
from execution.position_manager import PositionManager
from execution.reconciliation import ReconciliationManager
from reports.metrics import MetricsCalculator
from risk.brakes import BrakeManager
from risk.portfolio_manager import PortfolioManager
from risk.risk_manager import RiskManager
from storage.database import Database
from tg_bot.alerts import TelegramAlerts
from tg_bot.bot import TelegramBotHandler

logger = None  # initialized after logging setup


class Bitana:
    """Main application orchestrator."""

    def __init__(self, config: AppConfig) -> None:
        self.cfg = config
        self.start_time = time.time()
        self._running = False
        self._shutdown_event = asyncio.Event()

        # Components (initialized in setup)
        self.db: Database = Database(config.database.path)
        self.rate_limiter = RateLimiterGroup(
            order_weight_per_min=config.rate_limiter.order_weight_per_minute,
            data_weight_per_min=config.rate_limiter.data_weight_per_minute,
            warn_threshold_pct=config.rate_limiter.warn_threshold_pct,
        )
        self.rest_client = BinanceRestClient(
            api_key=config.secrets.binance_api_key,
            api_secret=config.secrets.binance_api_secret,
            testnet=config.secrets.binance_testnet,
            rate_limiter=self.rate_limiter,
        )
        self.symbol_info = SymbolInfoManager()
        self.candle_mgr = CandleManager(config.data.candle_history_limit)
        self.ws = BinanceWebSocket(
            testnet=config.secrets.binance_testnet,
            max_retries=config.data.ws_reconnect_max_retries,
            base_delay_s=config.data.ws_reconnect_base_delay_s,
        )
        self.risk_mgr = RiskManager(config)
        self.brake_mgr = BrakeManager(config.brakes)
        self.portfolio_mgr = PortfolioManager(config.portfolio)
        self.regime_filter = RegimeFilter(config.regime_filters)
        self.alerts = TelegramAlerts(
            config.secrets.telegram_bot_token,
            config.secrets.telegram_chat_id,
        )
        self.trade_logger = TradeLogger(config.logging.trade_file)

        # Executor (set in setup)
        self.executor: BaseExecutor = None  # type: ignore
        self.order_mgr: OrderManager = None  # type: ignore
        self.position_mgr: PositionManager = None  # type: ignore
        self.recon_mgr: ReconciliationManager = None  # type: ignore

        # Engines
        self.engines = {}
        self.burst_follow_state: dict[str, BurstFollowState] = {}
        self.force_pipeline: ForceOrderPipeline | None = None
        self._btc_regime: str | None = None
        self._btc_regime_dist: float | None = None
        self._last_btc_regime_fetch: float = 0.0
        self.health_server: HealthServer = None  # type: ignore
        self.watchdog = Watchdog(config.watchdog.heartbeat_interval_s)
        self.telegram_bot: TelegramBotHandler = None  # type: ignore

    async def setup(self) -> None:
        """Initialize all components."""
        global logger
        logger = get_logger("main")

        logger.info(
            "Bitana starting",
            mode=self.cfg.mode,
            config_version=self.cfg.config_version,
            config_checksum=self.cfg.config_checksum[:16],
            symbols=self.cfg.symbols.active,
        )

        # Database
        await self.db.initialize()

        # Recover state
        risk_row = await self.db.get_risk_state()
        if risk_row:
            self.risk_mgr.state = RiskState(**{
                k: v for k, v in risk_row.items()
                if k in RiskState.model_fields
            })
            self.risk_mgr.normalize_active_risk()
            logger.info("Risk state recovered", equity=self.risk_mgr.state.current_equity)

        brake_row = await self.db.get_brake_state()
        if brake_row:
            self.brake_mgr.state = BrakeState(**{
                k: v for k, v in brake_row.items()
                if k in BrakeState.model_fields
            })
            logger.info("Brake state recovered", paused=self.brake_mgr.state.is_paused)

        # REST client
        await self.rest_client.start()

        # Alerting must be verified before live execution is considered ready.
        await self.alerts.initialize()

        # Symbol info
        exchange_info = await self.rest_client.get_exchange_info()
        if exchange_info:
            self.symbol_info.load_from_exchange_info(exchange_info)

        # Executor
        if self.cfg.mode == "live":
            self.executor = LiveExecutor(
                self.rest_client, self.symbol_info, self.cfg,
            )
        else:
            balance = 1000.0  # default paper balance
            if self.risk_mgr.state.current_equity > 0:
                balance = self.risk_mgr.state.current_equity
            self.executor = PaperExecutor(self.cfg, initial_balance=balance)

        self.order_mgr = OrderManager(
            self.executor, self.symbol_info, self.cfg, self.db, self.alerts,
        )
        self.position_mgr = PositionManager(
            self.order_mgr, self.cfg, self.db,
        )
        self.recon_mgr = ReconciliationManager(
            self.executor, self.position_mgr, self.cfg, self.db,
        )

        if self.cfg.mode == "live":
            ready, preflight_reason = await self._verify_live_preconditions()
            if not ready:
                pause_reason = f"Live preflight failed: {preflight_reason}"
                self.brake_mgr.pause(pause_reason)
                await self.db.save_brake_state(self.brake_mgr.state)
                logger.critical("Live execution disabled", reason=preflight_reason)
                await self.alerts.critical(
                    f"<b>LIVE EXECUTION DISABLED</b>\n{preflight_reason}\n"
                    "Trading is paused; no entries will be attempted."
                )
            elif (
                self.brake_mgr.state.is_paused
                and self.brake_mgr.state.pause_reason.startswith("Live preflight failed:")
            ):
                self.brake_mgr.resume()
                await self.db.save_brake_state(self.brake_mgr.state)
                await self.alerts.info(
                    "<b>Live preflight recovered</b>\nExecution permission and Telegram checks passed."
                )

        # Recover positions
        await self.position_mgr.recover_positions()

        # Sync live equity; clear stale peak/shutdown from account migration (testnet→mainnet)
        if self.cfg.mode == "live":
            try:
                balance = await self.executor.get_balance()
                if balance > 0:
                    peak = self.risk_mgr.state.peak_equity
                    if peak > balance * 1.5:
                        logger.warning(
                            "Resetting stale peak_equity",
                            old_peak=peak, equity=balance,
                        )
                        self.risk_mgr.state.peak_equity = balance
                    self.risk_mgr.update_equity(balance)
                    if (
                        self.brake_mgr.state.is_shutdown
                        and self.risk_mgr.state.current_drawdown_pct
                        < self.cfg.brakes.equity_shutdown_drawdown
                    ):
                        logger.warning(
                            "Clearing stale equity shutdown",
                            reason=self.brake_mgr.state.shutdown_reason,
                        )
                        self.brake_mgr.state.is_shutdown = False
                        self.brake_mgr.state.shutdown_reason = ""
                    await self.db.save_risk_state(self.risk_mgr.state)
                    await self.db.save_brake_state(self.brake_mgr.state)
            except Exception as e:
                logger.error("Startup equity sync failed", error=str(e))

        # Engines
        burst_enabled = (
            self.cfg.engines.burst_follow_enabled and self.cfg.burst_follow.enabled
        )
        for sym in self.cfg.symbols.active:
            resolved = resolve_symbol_config(self.cfg, sym)
            self.burst_follow_state[sym] = BurstFollowState()
            sym_engines: dict = {"risk_pct": resolved.risk_pct}
            if self.cfg.engines.compression_enabled:
                sym_engines["compression"] = CompressionBreakoutEngine(resolved.compression)
            if self.cfg.engines.squeeze_enabled and self.cfg.squeeze.enabled:
                sym_engines["squeeze"] = SqueezeEngine(self.cfg.squeeze)
            if burst_enabled and resolved.burst_follow.enabled:
                sym_engines["burst_follow"] = LiqBurstFollowEngine(resolved.burst_follow)
                sym_engines["burst_follow_cfg"] = resolved.burst_follow
            self.engines[sym] = sym_engines

        if burst_enabled:
            bf_cfg = self.cfg.burst_follow
            self.force_pipeline = ForceOrderPipeline(
                db_path=Path(bf_cfg.force_order_db_path),
                symbols=self.cfg.symbols.active,
                read_only=bf_cfg.force_order_read_only,
                liq_cache_db_path=Path(bf_cfg.liq_cache_db_path),
            )
            logger.info(
                "Burst-follow enabled",
                symbols=len(self.cfg.symbols.active),
                min_vol=bf_cfg.min_burst_volume_30m,
                force_order_read_only=bf_cfg.force_order_read_only,
                btc_regime_gate=bf_cfg.btc_regime_gate_enabled,
                allowed_btc_regimes=bf_cfg.allowed_btc_regimes,
            )
            if bf_cfg.btc_regime_gate_enabled:
                await self._refresh_btc_regime()

        # Load candle history
        for sym in self.cfg.symbols.active:
            for tf in [self.cfg.timeframes.regime, self.cfg.timeframes.primary, self.cfg.timeframes.confirmation]:
                await self.candle_mgr.load_history_from_rest(
                    self.rest_client, sym, tf,
                )

        # WebSocket
        self.ws.on_kline(self.candle_mgr.handle_ws_kline)

        # Health server
        if self.cfg.health.enabled:
            self.health_server = HealthServer(
                host=self.cfg.health.host, port=self.cfg.health.port,
            )
            self.health_server.set_mode(self.cfg.mode)
            self.health_server.set_metrics_getter(self._get_metrics_snapshot)
            await self.health_server.start()

        # Telegram
        self.telegram_bot = TelegramBotHandler(
            self.cfg.secrets.telegram_bot_token,
            self.cfg.secrets.telegram_chat_id,
            self.cfg.telegram.flatten_confirm_timeout_s,
        )
        self.telegram_bot.set_callbacks(
            state_getter=self._get_state_snapshot,
            flatten_callback=self._flatten_all,
            pause_callback=lambda: self.brake_mgr.pause("Telegram /pause"),
            resume_callback=self._resume_trading,
            shutdown_callback=lambda: self._shutdown_event.set(),
        )

        # Subscribe to events
        event_bus.subscribe(Events.CANDLE_CLOSED, self._on_candle_closed)
        event_bus.subscribe(Events.BRAKE_TRIGGERED, self._on_brake_triggered)

        logger.info("Setup complete")

    async def run(self) -> None:
        """Start all tasks and run until shutdown."""
        self._running = True

        # Register supervised tasks
        listen_key = ""
        if self.cfg.mode == "live":
            listen_key = await self.rest_client.create_listen_key()

        async def ws_task():
            await self.ws.start(
                symbols=self.cfg.symbols.active,
                timeframes=[
                    self.cfg.timeframes.regime,
                    self.cfg.timeframes.primary,
                    self.cfg.timeframes.confirmation,
                ],
                listen_key=listen_key,
            )
            # Keep alive
            while self._running:
                await asyncio.sleep(1)
                self.watchdog.heartbeat("websocket")

        async def recon_task():
            while self._running:
                await asyncio.sleep(self.cfg.reconciliation.interval_s)
                self.watchdog.heartbeat("reconciliation")
                await self.recon_mgr.reconcile()

        async def candle_verify_task():
            while self._running:
                await asyncio.sleep(self.cfg.data.rest_candle_check_interval_s)
                self.watchdog.heartbeat("candle_verify")
                for sym in self.cfg.symbols.active:
                    for tf in [self.cfg.timeframes.primary, self.cfg.timeframes.regime]:
                        await self.candle_mgr.verify_with_rest(
                            self.rest_client, sym, tf,
                        )

        async def time_sync_task():
            while self._running:
                await asyncio.sleep(self.cfg.data.server_time_sync_interval_s)
                self.watchdog.heartbeat("time_sync")
                await self.rest_client.sync_time()

        async def equity_update_task():
            while self._running:
                await asyncio.sleep(30)
                self.watchdog.heartbeat("equity_update")
                try:
                    if (
                        self.cfg.burst_follow.btc_regime_gate_enabled
                        and time.monotonic() - self._last_btc_regime_fetch >= 3600
                    ):
                        await self._refresh_btc_regime()
                    balance = await self.executor.get_balance()
                    if balance > 0:
                        self.risk_mgr.update_equity(balance)
                        await self.db.save_risk_state(self.risk_mgr.state)
                        # Check equity brakes
                        brakes = self.brake_mgr.check_equity_brakes(
                            self.risk_mgr.state.current_drawdown_pct
                        )
                        for b in brakes:
                            await event_bus.emit(Events.BRAKE_TRIGGERED, brake=b)
                except Exception as e:
                    logger.error("Equity update failed", error=str(e))

        async def telegram_task():
            await self.telegram_bot.start()
            while self._running:
                await asyncio.sleep(5)
                self.watchdog.heartbeat("telegram")

        async def listen_key_task():
            while self._running:
                await asyncio.sleep(1800)  # 30 min
                self.watchdog.heartbeat("listen_key")
                try:
                    await self.rest_client.keepalive_listen_key()
                except Exception:
                    pass

        self.watchdog.register("websocket", ws_task, critical=True)
        self.watchdog.register("reconciliation", recon_task, critical=True)
        self.watchdog.register("candle_verify", candle_verify_task, critical=False)
        self.watchdog.register("time_sync", time_sync_task, critical=False)
        self.watchdog.register("equity_update", equity_update_task, critical=True)
        self.watchdog.register("telegram", telegram_task, critical=False)
        if self.cfg.mode == "live":
            self.watchdog.register("listen_key", listen_key_task, critical=False)

        if self.force_pipeline is not None and not self.force_pipeline.read_only:
            async def force_order_task():
                ws_task = asyncio.create_task(
                    self.force_pipeline.run_ws_loop(self._shutdown_event),
                )
                try:
                    while not self._shutdown_event.is_set():
                        self.watchdog.heartbeat("force_orders")
                        await asyncio.sleep(15)
                finally:
                    if not ws_task.done():
                        ws_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await ws_task

            self.watchdog.register("force_orders", force_order_task, critical=True)

        await self.watchdog.start_all()

        # Send startup alert without implying that a paused live bot is trade-ready.
        if self.brake_mgr.state.is_paused:
            await self.alerts.warning(
                f"<b>Bitana Started PAUSED</b>\nReason: {self.brake_mgr.state.pause_reason}"
            )
        else:
            await self.alerts.startup_alert(self.cfg.mode, self.cfg.config_checksum)

        logger.info("All tasks started, entering main loop")

        # Wait for shutdown signal
        await self._shutdown_event.wait()
        await self.shutdown()

    async def _verify_live_preconditions(self) -> tuple[bool, str]:
        """Fail closed unless alert delivery and no-fill order auth both work."""
        failures = []
        if not self.cfg.telegram.enabled:
            failures.append("Telegram alerting is disabled")
        elif not await self.alerts.verify():
            failures.append("Telegram bot/chat verification failed")

        order_ok, response = await self.rest_client.test_order_permission()
        if not order_ok:
            code = response.get("code", "unknown")
            msg = response.get("msg", "unknown error")
            failures.append(f"Binance futures order test rejected ({code}: {msg})")

        return not failures, "; ".join(failures)

    async def _resume_trading(self) -> tuple[bool, str]:
        """Re-run live preflight before accepting a Telegram /resume."""
        if self.cfg.mode == "live":
            ready, reason = await self._verify_live_preconditions()
            if not ready:
                self.brake_mgr.pause(f"Live preflight failed: {reason}")
                await self.db.save_brake_state(self.brake_mgr.state)
                await self.alerts.critical(
                    f"<b>RESUME REFUSED</b>\n{reason}"
                )
                return False, reason

        self.brake_mgr.resume()
        await self.db.save_brake_state(self.brake_mgr.state)
        return True, ""

    async def _refresh_btc_regime(self) -> None:
        """Load BTC 4h bars and compute bull/bear/neutral (shadow-aligned)."""
        try:
            await self.candle_mgr.load_history_from_rest(
                self.rest_client, "BTCUSDT", "4h", limit=250,
            )
            candles = self.candle_mgr.get_candles("BTCUSDT", "4h")
            state, dist = compute_btc_regime(candles)
            self._btc_regime = state
            self._btc_regime_dist = dist
            self._last_btc_regime_fetch = time.monotonic()
            logger.info(
                "BTC regime updated",
                state=state,
                dist_pct=dist,
                bars=len(candles),
            )
        except Exception as e:
            logger.warning("BTC regime refresh failed", error=str(e))

    async def _on_candle_closed(self, **kwargs) -> None:
        """Main trading logic — triggered on each closed candle."""
        symbol = kwargs.get("symbol", "")
        timeframe = kwargs.get("timeframe", "")
        candle: Candle = kwargs.get("candle")

        if not candle or not symbol:
            return

        # Only process on primary (5m) timeframe close
        if timeframe != self.cfg.timeframes.primary:
            # But manage positions on all 5m closes
            if timeframe == self.cfg.timeframes.primary:
                pass  # handled below
            return

        # Update paper executor price + force-order price cache
        if isinstance(self.executor, PaperExecutor):
            self.executor.set_price(symbol, candle.close)
        if self.force_pipeline is not None:
            self.force_pipeline.set_price(symbol, candle.close)

        # 1. Manage existing positions
        candles_5m = self.candle_mgr.get_candles(symbol, self.cfg.timeframes.primary)
        closed_trades = await self.position_mgr.manage_on_candle_close(
            symbol, candle, candles_5m,
        )
        for trade in closed_trades:
            self.trade_logger.log_trade(trade.model_dump())
            self.risk_mgr.record_trade_result(trade.pnl_r)

            # Record loss for brakes
            if trade.pnl_usd < 0 and self.risk_mgr.state.current_equity > 0:
                loss_pct = abs(trade.pnl_usd) / self.risk_mgr.state.current_equity
                triggered = self.brake_mgr.record_loss(loss_pct)
                for b in triggered:
                    await event_bus.emit(Events.BRAKE_TRIGGERED, brake=b)

            await self.alerts.exit_alert(
                trade.symbol, trade.side.value, trade.exit_price,
                trade.pnl_usd, trade.pnl_r, trade.exit_reason,
            )
            await self.db.save_risk_state(self.risk_mgr.state)
            await self.db.save_brake_state(self.brake_mgr.state)

        # 2. Check if entries allowed
        allowed, reason = self.brake_mgr.check_entry_allowed()
        if not allowed:
            logger.info("Entries blocked", reason=reason)
            return

        # 3. Regime filter
        candles_15m = self.candle_mgr.get_candles(symbol, self.cfg.timeframes.regime)
        tradeable, filter_reason = self.regime_filter.check(
            symbol, candles_15m,
        )
        if not tradeable:
            logger.debug("Regime filter rejected", symbol=symbol, reason=filter_reason)
            return

        # 4. Evaluate engines
        candles_1m = self.candle_mgr.get_candles(symbol, self.cfg.timeframes.confirmation)
        signals: list[Signal] = []

        sym_engines = self.engines.get(symbol, {})
        engine_names: list[str] = []
        if self.cfg.engines.compression_enabled:
            engine_names.append("compression")
        if self.cfg.engines.squeeze_enabled and self.cfg.squeeze.enabled:
            engine_names.append("squeeze")
        if self.cfg.engines.burst_follow_enabled and sym_engines.get("burst_follow"):
            engine_names.append("burst_follow")

        burst_stats = None
        if self.force_pipeline is not None and "burst_follow" in engine_names:
            if self.force_pipeline.read_only:
                self.force_pipeline.refresh_cascades([symbol])
            burst_stats = self.force_pipeline.intraday_burst_stats(symbol, candle)
            bf_cfg = sym_engines.get("burst_follow_cfg")
            min_vol = bf_cfg.min_burst_volume_30m if bf_cfg else self.cfg.burst_follow.min_burst_volume_30m
            min_ev = bf_cfg.min_burst_events_30m if bf_cfg else self.cfg.burst_follow.min_burst_events_30m
            if (
                burst_stats.get("volume_30m", 0) >= min_vol
                and burst_stats.get("events_30m", 0) >= min_ev
            ):
                ForceOrderPipeline.sync_burst_state(
                    self.burst_follow_state[symbol],
                    burst_stats,
                    symbol,
                    self.force_pipeline.cascade_engine,
                )

        for engine_name in engine_names:
            engine = sym_engines.get(engine_name)
            if not engine:
                continue
            try:
                if engine_name == "burst_follow":
                    if burst_stats is None:
                        continue
                    bf_cfg = sym_engines.get("burst_follow_cfg") or self.cfg.burst_follow
                    btc_regime = (
                        self._btc_regime
                        if bf_cfg.btc_regime_gate_enabled else None
                    )
                    sig = await engine.evaluate(
                        symbol, candles_5m, candles_15m, candles_1m,
                        self.burst_follow_state[symbol],
                        burst=burst_stats,
                        btc_regime=btc_regime,
                    )
                else:
                    sig = await engine.evaluate(symbol, candles_5m, candles_15m, candles_1m)
                if sig:
                    signals.append(sig)
            except Exception as e:
                logger.error("Engine error", engine=engine_name, symbol=symbol, error=str(e))

        if not signals:
            return

        # 5. Portfolio filter and priority
        signals = self.portfolio_mgr.prioritize_signals(signals)
        open_positions = self.position_mgr.get_open_positions()

        for sig in signals:
            can_open, reason = self.portfolio_mgr.can_open(sig, open_positions)
            if not can_open:
                logger.info("Portfolio rejected", symbol=sig.symbol, reason=reason)
                continue

            # 6. Spread check
            spread_ok, spread_bps = await self.order_mgr.check_spread(
                sig.symbol, self.rest_client if self.cfg.mode == "live" else None,
            )
            if not spread_ok:
                await self.alerts.warning(
                    f"Trade skipped: spread {spread_bps:.1f}bps > max {self.cfg.execution.max_spread_bps}bps"
                )
                continue

            # 7. Size position
            equity = await self.executor.get_balance()
            sym_risk = sym_engines.get("risk_pct", self.cfg.risk.default_risk_pct)
            if sig.engine == EngineType.LIQ_BURST_FOLLOW:
                bf_cfg = sym_engines.get("burst_follow_cfg")
                if bf_cfg is not None:
                    sym_risk = bf_cfg.risk_pct
            sizing_mult = self.portfolio_mgr.get_sizing_multiplier(sig, open_positions)

            quantity, leverage = self.risk_mgr.calculate_position_size(
                equity, sig.entry_price, sig.stop_price, sym_risk,
            )
            quantity *= sizing_mult

            if quantity <= 0 or leverage <= 0:
                logger.warning("Zero position size", symbol=sig.symbol)
                continue

            # 8. Execute entry
            await self.db.save_signal(sig)
            result = await self.order_mgr.execute_entry(sig, quantity, leverage)
            if not result:
                if self.order_mgr.last_soft_reject:
                    # Insufficient margin for another concurrent position:
                    # skip this signal, keep trading the rest of the session.
                    continue
                if self.cfg.mode == "live":
                    reason = f"Entry execution failed for {sig.symbol}; manual review required"
                    self.brake_mgr.pause(reason)
                    await self.db.save_brake_state(self.brake_mgr.state)
                    logger.critical("Live trading paused after entry failure", symbol=sig.symbol)
                    await self.alerts.critical(
                        f"<b>LIVE TRADING PAUSED</b>\n{reason}"
                    )
                continue

            # Live execution telemetry (signal price vs fill, spread at entry)
            expected_entry = sig.entry_price
            fill_entry = result.avg_fill_price
            entry_slippage_bps = 0.0
            if expected_entry > 0 and fill_entry > 0:
                raw_bps = (fill_entry - expected_entry) / expected_entry * 10000
                entry_slippage_bps = raw_bps if sig.side == Side.LONG else -raw_bps
            sig.signal_data["expected_entry"] = expected_entry
            sig.signal_data["spread_bps_at_entry"] = spread_bps
            sig.signal_data["entry_slippage_bps"] = entry_slippage_bps

            # 9. Create position
            entry_atr = float(sig.signal_data.get("entry_atr") or 0.0)
            pos = Position(
                trade_uuid=sig.trade_uuid,
                symbol=sig.symbol,
                side=sig.side,
                engine=sig.engine,
                state=PositionState.FILLED,
                entry_price=result.avg_fill_price,
                entry_time=datetime.now(timezone.utc),
                quantity=result.filled_qty,
                leverage=leverage,
                stop_price=sig.stop_price,
                initial_stop=sig.stop_price,
                risk_r=abs(result.avg_fill_price - sig.stop_price),
                commission_total=result.commission,
                client_order_ids=[result.client_order_id],
                signal_data=sig.signal_data,
                entry_atr=entry_atr,
            )

            if (
                not sig.signal_data.get("time_exit_only")
                and sig.engine != EngineType.LIQ_BURST_FOLLOW
            ):
                risk_dist = abs(result.avg_fill_price - sig.stop_price)
                tp1_r = self.cfg.profit_taking.partial_close_r
                if sig.side == Side.LONG:
                    pos.tp1_price = result.avg_fill_price + risk_dist * tp1_r
                else:
                    pos.tp1_price = result.avg_fill_price - risk_dist * tp1_r

            pos.transition_to(PositionState.STOP_PLACED)
            pos.transition_to(PositionState.MANAGING)

            await self.position_mgr.add_position(pos)
            open_positions.append(pos)

            await self.alerts.entry_alert(
                sig.symbol, sig.side.value, result.avg_fill_price,
                result.filled_qty, sig.engine.value, sig.trade_uuid,
            )

            logger.info(
                "Position opened",
                trade_uuid=sig.trade_uuid,
                symbol=sig.symbol, side=sig.side.value,
                entry=result.avg_fill_price, stop=sig.stop_price,
                qty=result.filled_qty, leverage=leverage,
                spread_bps=round(spread_bps, 2),
                entry_slippage_bps=round(entry_slippage_bps, 2),
            )
            break  # One entry per candle per symbol

    async def _on_brake_triggered(self, **kwargs) -> None:
        brake = kwargs.get("brake")
        if brake:
            await self.alerts.brake_alert(str(brake), f"Brake triggered: {brake}")
            await self.db.save_brake_state(self.brake_mgr.state)

    async def _flatten_all(self) -> None:
        """Emergency: close all positions, cancel all orders, pause."""
        logger.critical("FLATTEN ALL triggered")
        for sym in self.cfg.symbols.active:
            await self.executor.cancel_all_orders(sym)

        for pos in self.position_mgr.get_open_positions():
            await self.executor.close_position(
                pos.symbol, pos.side.value, pos.quantity,
            )
            pos.transition_to(PositionState.CLOSING)
            pos.transition_to(PositionState.CLOSED)
            await self.db.save_position(pos)

        self.brake_mgr.pause("Flatten all via Telegram")
        await self.db.save_brake_state(self.brake_mgr.state)
        await self.alerts.critical("ALL POSITIONS FLATTENED — trading paused")

    def _get_state_snapshot(self) -> dict:
        """State for Telegram /status command."""
        uptime = time.time() - self.start_time
        h, r = divmod(int(uptime), 3600)
        m, s = divmod(r, 60)

        positions_detail = []
        for pos in self.position_mgr.get_open_positions():
            positions_detail.append({
                "symbol": pos.symbol,
                "side": pos.side.value,
                "entry": pos.entry_price,
                "qty": pos.quantity,
                "stop": pos.stop_price,
                "candles": pos.candles_held,
                "current_r": 0,  # would need current price
            })

        return {
            "mode": self.cfg.mode,
            "equity": self.risk_mgr.state.current_equity,
            "drawdown": self.risk_mgr.state.current_drawdown_pct,
            "open_positions": len(self.position_mgr.get_open_positions()),
            "paused": self.brake_mgr.state.is_paused,
            "pause_reason": self.brake_mgr.state.pause_reason,
            "task_health": "ok",
            "uptime": f"{h}h {m}m {s}s",
            "positions_detail": positions_detail,
            "risk_state": {
                "risk_pct": self.risk_mgr.state.risk_pct_active,
                "peak_equity": self.risk_mgr.state.peak_equity,
                "drawdown": self.risk_mgr.state.current_drawdown_pct,
                "consecutive_losses": self.risk_mgr.state.consecutive_losses,
                "reduced_trades": self.risk_mgr.state.reduced_risk_trades_remaining,
            },
            "stats": {},
        }

    def _get_metrics_snapshot(self) -> dict:
        """Metrics for health endpoint."""
        return {
            "open_positions": len(self.position_mgr.get_open_positions()),
            "equity": self.risk_mgr.state.current_equity,
            "drawdown_pct": self.risk_mgr.state.current_drawdown_pct,
            "paused": self.brake_mgr.state.is_paused,
            "ws_connected": self.ws.is_connected,
            "task_health": self.watchdog.get_health_summary(),
        }

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down...")
        self._running = False

        await self.alerts.warning("Bitana shutting down...")

        # Stop watchdog tasks
        await self.watchdog.stop_all()

        # Stop WebSocket
        await self.ws.stop()

        # Stop Telegram
        if self.telegram_bot:
            await self.telegram_bot.stop()

        # Stop health server
        if self.health_server:
            await self.health_server.stop()

        # Persist final state
        await self.db.save_risk_state(self.risk_mgr.state)
        await self.db.save_brake_state(self.brake_mgr.state)
        await self.db.set_system_state(
            "last_shutdown", datetime.now(timezone.utc).isoformat(),
        )
        await self.db.set_system_state("config_checksum", self.cfg.config_checksum)

        # Close REST
        if self.force_pipeline is not None:
            self.force_pipeline.close()
        await self.rest_client.close()

        # Close DB
        await self.db.close()

        logger.info("Shutdown complete")


async def async_main(mode: str | None = None, config_path: str | Path = "config/settings.yaml") -> None:
    """Async entry point."""
    config = load_config(config_path)
    if mode:
        config.mode = mode

    setup_logging(
        level=config.logging.level,
        log_file=config.logging.file,
        max_bytes=config.logging.max_bytes,
        backup_count=config.logging.backup_count,
        retention_days=config.logging.retention_days,
    )

    app = Bitana(config)

    # Signal handlers
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: app._shutdown_event.set())

    await app.setup()
    await app.run()


def main():
    parser = argparse.ArgumentParser(description="Bitana Trading Bot")
    parser.add_argument(
        "--mode", choices=["paper", "live"],
        help="Override mode from config",
    )
    parser.add_argument(
        "--config",
        default="config/settings.yaml",
        help="Path to YAML config file",
    )
    args = parser.parse_args()

    asyncio.run(async_main(mode=args.mode, config_path=args.config))


if __name__ == "__main__":
    main()
