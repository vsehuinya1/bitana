# Bitana — Production Binance Futures Trading Bot

Aggressive, systematic USDT-M Futures satellite trading system for small accounts.
Compression breakout engine, full risk management, Telegram oversight, paper/live modes.

## Quick Start

```bash
# Clone and setup
cd bitana
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your Binance API + Telegram credentials
# Review config/settings.yaml

# Run paper mode
python main.py --mode paper
```

## Architecture

```
main.py              ← Orchestrator: startup, main loop, shutdown
config/              ← Pydantic-validated config with SHA-256 checksumming
core/                ← Models, events, logging, watchdog, health endpoint
data/                ← Binance REST/WS, candle management, symbol precision, rate limiter
engines/             ← Signal engines (compression breakout + squeeze stub)
risk/                ← Risk manager, hard brakes, portfolio manager
execution/           ← Unified executor (paper/live), order/position management, reconciliation
telegram/            ← Bot commands + tiered alerts
reports/             ← Metrics calculator + CLI display
storage/             ← SQLite persistence (single-writer queue, WAL mode)
tools/               ← Replay runner for historical testing
deploy/              ← systemd + Docker deployment
tests/               ← Unit tests
```

## Key Design Decisions

| Decision | Implementation |
|----------|---------------|
| **Unified Executor** | Paper and live share identical `BaseExecutor` interface |
| **SQLite Writes** | Single async writer queue — no lock contention |
| **Candle Truth** | WebSocket primary, REST authoritative (auto-corrects mismatches) |
| **Position Lifecycle** | Explicit state machine: `SIGNAL→ORDER→FILLED→MANAGING→CLOSED` |
| **Per-Symbol Config** | Deep-merged overrides (e.g., BTC gets 8-candle compression, lower risk) |
| **Trade Linking** | End-to-end UUID from signal through exit |
| **Config Versioning** | SHA-256 checksum logged on every startup |

## Configuration

All parameters in `config/settings.yaml`. Key sections:

- **symbols**: Active pairs + per-symbol overrides
- **risk**: Default 1.5% per trade, 0.75% on drawdown >15%, 10x leverage cap
- **brakes**: Daily 4%, weekly 8% loss limits, 48h cooldown, 25%/40% DD pauses
- **profit_taking**: 50% at +1.5R, trail remainder with 1×ATR on 5m
- **regime_filters**: 15m ATR minimum, session windows, blackout windows
- **execution**: Spread/slippage limits, partial fill timeout

## Telegram Commands

| Command | Action |
|---------|--------|
| `/status` | Equity, DD, positions, pause state, uptime |
| `/positions` | Open position details |
| `/stats` | Win rate, expectancy, profit factor |
| `/risk` | Active risk %, peak equity, streak |
| `/pause` | Pause new entries |
| `/resume` | Resume trading (also clears manual review) |
| `/shutdown` | Graceful shutdown |
| `/flatten` | Emergency: close all + cancel all + pause |
| `/logs` | Recent log entries |

## Deployment

### systemd (recommended)

```bash
# On Ubuntu VPS (Frankfurt)
chmod +x deploy/setup.sh
sudo ./deploy/setup.sh

# Edit credentials
sudo nano /opt/bitana/.env

# Start
sudo systemctl start bitana
sudo journalctl -u bitana -f

# Health check
curl http://localhost:8080/health
```

### Docker

```bash
cp .env.example .env
# Edit .env
docker-compose up -d
docker-compose logs -f
```

## Paper Mode

Paper mode simulates fills with:
- Taker fee: 0.04% (configurable)
- Random slippage: ~2bps (configurable)
- Same code paths as live mode
- Full state persistence

**Run paper for ≥1 week before live deployment.**

## Live Deployment Checklist

1. ☐ Paper mode run for ≥1 week with no crashes
2. ☐ Telegram commands all responding
3. ☐ Health endpoint accessible
4. ☐ Config reviewed (especially risk %, leverage cap, brakes)
5. ☐ `.env` has production API keys (NOT testnet)
6. ☐ `BINANCE_TESTNET=false` in `.env`
7. ☐ `mode: live` in settings.yaml OR `--mode live` flag
8. ☐ systemd service set to `--mode live`
9. ☐ VPS time synced (NTP)
10. ☐ Backup `.env` and `data/bitana.db` location noted

## Testing

```bash
python -m pytest tests/ -v
```

## Replay Runner

Test engines against historical data:

```bash
python -m tools.replay_runner \
    --symbol SOLUSDT \
    --start 2025-01-01 \
    --end 2025-06-01 \
    --engine compression \
    --output results.csv
```

## Top 10 Future Optimizations

1. **Squeeze engine (phase 2)** — OI + price velocity + liquidation cascade detection
2. **Walk-forward optimization** — Auto-tune engine thresholds on rolling windows
3. **Multi-exchange** — Add Bybit/OKX for arbitrage or better fills
4. **Funding rate strategy** — Trade funding rate differentials
5. **Machine learning regime classifier** — Replace rule-based regime filter
6. **Order book imbalance signals** — L2 data for entry timing
7. **Adaptive position sizing** — Kelly criterion with variance penalization
8. **Correlation matrix** — Dynamic cross-asset correlation tracking
9. **Web dashboard** — Real-time P&L, equity curve, trade log UI
10. **Backtesting framework** — Full backtest with realistic execution modeling

## Known Limitations

- **OI endpoint**: Binance `/fapi/v1/openInterest` is snapshot-only, no tick history
- **Funding fees**: Polled periodically, not real-time — ~15s latency
- **Paper mode**: Simulated fills may not reflect real liquidity/slippage
- **1m data volume**: ~288 candles/day/symbol — memory managed via rolling window limits
