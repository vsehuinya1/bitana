#!/usr/bin/env python3
"""Wick catcher + perp discount execution engine (owner order 2026-10-03: "$1 it is ... I'll want both strategies, and a
sub account, with a smaller balance. Disaster stop sounds like a safe idea").

Strategies = the paper readers of record (research/wick_catcher_reader.py k=5 primary book; research/
perp_discount_reader.py 4h primary book), on the same 20 coins, with the v2 changes (owner order 2026-10-04 "Build into
the engine"; edge report "Improving the wick/discount engine": each passed alone, the combination was tested once):
  wick      every hour: a LADDER of buys 5 / 6.5 / 8 x ATR1h under the last hourly close (1/3 R each), live for that
            hour only; each rung exits at the pre-wick close (take-profit) or 24h after its fill. ADD-ON: when BTC's
            close at the end of the rung's fill bar is >= 1.7% under the bid-hour close, a second unit of the same size
            is bought at market, exiting with its rung (same take-profit, same expiry).
  discount  every 5m close: perp/spot basis crosses below -30 bps AND BTC's 5m close is >= 1% under 60 min earlier ->
            buy the perp now; exit 4h later.
Coins where a 1/3-R rung is below Binance's minimum order value (at $1/R: BTC, LINK, LTC, BCH, AAVE ... depending on
  ATR) take the v1 single 5-ATR bid at the full R instead, so every coin stays at ~r_usd per R.
Sizing: 1R = 3 x ATR1h (the paper unit); each leg risks r_usd per R (qty = r_usd / (3 x ATR1h)).
Disaster stop: stop_r R under the entry on every leg (catastrophe-only; reports/structural_edge_2026-09-25.md
  "Disaster stop" + correction: stops inside the normal adverse range lose money; the deepest intra-trade drop in 6
  years on these coins was -16.6R (wick) / -6.0R (discount), so the default sits at -20R).
  After a stop-out the coin gets a cooldown (no new entry for that strategy) of cooldown_h hours.
One leg per coin per strategy (as on paper). Both strategies may hold the same coin: every exit is reduce-only with the
  leg's own quantity, so legs never close each other (one-way mode).
Guards: PAUSE file (data/wd_engine.PAUSE -> no new entries; open legs keep their exits), equity floor (live), max legs.

Modes (config `mode`): dry = public data only, fills simulated on 1m bars, no keys, no account calls;
  testnet = Binance futures testnet keys (API plumbing test); live = the sub-account keys in env_file
  (WD_API_KEY / WD_API_SECRET; never logged). Going testnet/live is the owner's step.
Known paper/live differences: live buys fill on a touch of the bid (paper needs low <= bid - 0.1 ATR) and pay taker
  fees on triggered entries; take-profit is a market exit triggered one tick above the pre-wick close; R and $ here use
  the paper cost model (wick 0.12%, discount 0.20% round trip), Binance's own fee lines are the truth.
Run: venv/bin/python -m wd_engine.engine --config wd_engine/config.yaml
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import aiohttp
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.logging_setup import get_logger, setup_logging  # noqa: E402
from wd_engine import rules  # noqa: E402
from wd_engine.exchange import DryBroker, LiveBroker, Market  # noqa: E402
from wd_engine.store import Store, now_iso  # noqa: E402

logger = get_logger("wd_engine")
COST = {"wick": 0.0012, "discount": 0.0020, "addon": 0.0020}   # paper cost models (add-on = taker in, like discount)
HOLD_H = {"wick": rules.WICK_HOLD_H, "discount": rules.DISC_HOLD_H}
UTC = timezone.utc
SETUP_EXIT = 2          # exit code for setup failures; the unit sets RestartPreventExitStatus=2 (one alert, no loop)


def _env(path: str, key: str) -> str | None:
    try:
        for ln in open(path):
            ln = ln.strip().removeprefix("export ")
            if ln.startswith(key + "="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def _ts(iso: str | None) -> datetime | None:
    return datetime.fromisoformat(iso) if iso else None


class Engine:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.mode = cfg["mode"]
        assert self.mode in ("dry", "testnet", "live"), "mode must be dry | testnet | live"
        self.syms: list[str] = cfg["universe"]
        self.r_usd = float(cfg["r_usd"])
        self.stop_r = float(cfg["stop_r"])
        self.store = Store(cfg["db"])
        self.market = Market()
        if self.mode == "dry":
            self.broker = DryBroker(self.market)
        else:
            key, sec = _env(cfg["env_file"], "WD_API_KEY"), _env(cfg["env_file"], "WD_API_SECRET")
            if not key or not sec:
                logger.error("keys missing", env_file=cfg["env_file"])
                raise SystemExit(SETUP_EXIT)         # setup problem: no systemd restart loop (RestartPreventExitStatus)
            self.broker = LiveBroker(key, sec, testnet=self.mode == "testnet")
        self.tag = {"dry": "WD[dry]", "testnet": "WD[testnet]", "live": "WD"}[self.mode]
        self.filters: dict = {}
        self.atr: dict[str, float] = {}
        self.close_h: dict[str, float] = {}
        self.dirty: set[str] = set()
        self.paused_reason: str | None = None
        self.fed_1m: dict[str, int] = {}
        self.stop = False
        self._sent: dict[str, float] = {}
        self.tg = (_env(cfg.get("telegram_env", "/root/bitana/.env"), "TELEGRAM_BOT_TOKEN"),
                   _env(cfg.get("telegram_env", "/root/bitana/.env"), "TELEGRAM_CHAT_ID")) if cfg.get("telegram") else (None, None)

    # ------------------------------------------------------------------------------------------- helpers
    def cid(self, leg_id: int, strategy: str, part: str) -> str:
        return f"wd{strategy[0]}{leg_id}-{int(time.time()) % 100000}{part}"          # <= 36 chars, unique per run

    async def notify(self, text: str, key: str | None = None, every_s: int = 0) -> None:
        logger.info("notify", text=text)
        tok, chat = self.tg
        if not tok or not chat:
            return
        if key and time.time() - self._sent.get(key, 0) < every_s:
            return
        if key:
            self._sent[key] = time.time()
        data = urllib.parse.urlencode({"chat_id": chat, "text": f"{self.tag}: {text}"[:4000]}).encode()

        def _send() -> None:
            try:
                urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=15).read()
            except Exception as e:                                     # never let alerts break trading
                logger.warning("telegram send failed", error=type(e).__name__)
        await asyncio.to_thread(_send)

    def entries_blocked(self) -> str | None:
        if os.path.exists(self.cfg["pause_file"]):
            return "pause file"
        if self.paused_reason:
            return self.paused_reason
        if len([x for x in self.store.live_legs() if x["state"] != "BID"]) >= int(self.cfg["max_open_legs"]):
            return "max open legs"
        return None

    def cooling(self, strategy: str, symbol: str) -> bool:
        t = _ts(self.store.last_stop_out(strategy, symbol))
        return bool(t and datetime.now(UTC) - t < timedelta(hours=float(self.cfg["cooldown_h"])))

    def holding(self, strategy: str, symbol: str, rung: float | None = None) -> bool:
        """An open leg on this coin for this strategy (wick: this rung; add-ons exit with their rung, so they don't count)."""
        return any(x["strategy"] == strategy and x["symbol"] == symbol and x["state"] in ("OPEN", "ENTERING")
                   and (rung is None or (x["rung"] == rung and not x["parent"])) for x in self.store.live_legs())

    def label(self, leg: dict) -> str:
        if leg["strategy"] != "wick":
            return leg["strategy"]
        return f"wick {leg['rung']:g} ATR" + (" add-on" if leg["parent"] else "")

    async def safe(self, coro, what: str) -> None:
        try:
            await coro
        except Exception as e:                                             # one failure never stops the loop
            logger.exception("step failed", step=what)
            await self.notify(f"error in {what}: {type(e).__name__}: {e}"[:300], key=f"err:{what}", every_s=900)

    # ------------------------------------------------------------------------------------------- market data
    async def refresh_atr(self) -> None:
        want = int((datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)).timestamp() * 1000)
        got = await asyncio.gather(*[self.market.klines(s, "1h", 17) for s in self.syms])
        for s, bars in zip(self.syms, got):
            if bars and bars[-1][0] < want:                                 # the just-closed hour not served yet
                await asyncio.sleep(2)
                bars = await self.market.klines(s, "1h", 17)
            if len(bars) >= 15 and bars[-1][0] >= want:
                self.atr[s] = rules.atr1h([b[1:5] for b in bars])
                self.close_h[s] = bars[-1][4]
            else:
                self.atr.pop(s, None)
                self.close_h.pop(s, None)
                logger.warning("no fresh hourly bar", symbol=s)

    # ------------------------------------------------------------------------------------------- leg lifecycle
    async def open_leg(self, leg_id: int, entry: float, qty: float, expires: str | None = None) -> None:
        leg = self.store.leg(leg_id)
        s, f = leg["symbol"], self.filters[leg["symbol"]]
        q = rules.floor_to(qty, f.step)
        now = datetime.now(UTC)
        stop = rules.floor_to(rules.stop_price(entry, leg["atr"], self.stop_r), f.tick)
        upd = dict(state="OPEN", entry=entry, entry_time=now.isoformat(timespec="seconds"), qty=str(q), stop=float(stop),
                   expires=expires or (now + timedelta(hours=HOLD_H[leg["strategy"]])).isoformat(timespec="seconds"))
        if leg["strategy"] == "wick" and not leg["parent"] and self.cfg.get("wick_addon_btc_dump") is not None:
            m5 = now.replace(second=0, microsecond=0)                          # add-on check at the fill bar's close
            upd.update(addon="pending", addon_at=(m5 - timedelta(minutes=m5.minute % 5) + timedelta(minutes=5)).isoformat())
        self.store.update(leg_id, **upd)
        self.store.event(leg_id, "open", f"{s} {q} @ {entry}")
        leg = self.store.leg(leg_id)
        risk = float(q) * rules.R_ATR * leg["atr"]
        await self.notify(f"{self.label(leg)} BUY {s[:-4]} {rules.fmt_dec(q)} @ {entry:.6g} (1R = ${risk:.2f}; stop "
                          f"{float(stop):.6g} = -{self.stop_r:g}R" + (f"; take-profit {leg['ref']:.6g}" if leg["strategy"] == "wick" else
                          f"; exit in {HOLD_H['discount']}h") + ")")
        await self.place_exits(leg_id)

    async def place_exits(self, leg_id: int) -> None:
        leg = self.store.leg(leg_id)
        s, f, q = leg["symbol"], self.filters[leg["symbol"]], Decimal(leg["qty"])
        if not leg["stop_cid"] and leg["stop"] and leg["stop"] > 0:
            cid = self.cid(leg_id, leg["strategy"], "s")
            r = await self.broker.exit_algo(s, "STOP_MARKET", q, Decimal(str(leg["stop"])), cid)
            if r.code == -2021:
                return await self.close_market(leg_id, "stop")
            if r.ok:
                self.store.update(leg_id, stop_cid=cid)
            else:
                self.store.event(leg_id, "error", f"stop rejected {r.code} {r.msg}")
                await self.notify(f"{s[:-4]} disaster stop REJECTED ({r.code} {r.msg}); retrying each minute", key=f"stoprej:{leg_id}", every_s=1800)
        if leg["strategy"] == "wick" and not leg["tp_cid"]:
            tp = rules.ceil_to(leg["ref"], f.tick) + Decimal(f.tick)        # trade-through: one tick above the pre-wick close
            cid = self.cid(leg_id, "wick", "t")
            r = await self.broker.exit_algo(s, "TAKE_PROFIT_MARKET", q, tp, cid)
            if r.code == -2021:
                return await self.close_market(leg_id, "tp")
            if r.ok:
                self.store.update(leg_id, tp_cid=cid)
            else:
                self.store.event(leg_id, "error", f"tp rejected {r.code} {r.msg}")

    async def close_market(self, leg_id: int, why: str) -> None:
        leg = self.store.leg(leg_id)
        if leg.get("state") != "OPEN":
            return
        s = leg["symbol"]
        for c in (leg["stop_cid"], leg["tp_cid"]):
            if c:
                await self.broker.cancel(s, "algo", c)
        r = await self.broker.market(s, "SELL", Decimal(leg["qty"]), True, self.cid(leg_id, leg["strategy"], "x"))
        if not r.ok or r.filled_qty <= 0:
            self.store.event(leg_id, "error", f"market exit failed {r.code} {r.msg}")
            await self.notify(f"{s[:-4]} {why} exit FAILED ({r.code} {r.msg}); will retry", key=f"exitfail:{leg_id}", every_s=600)
            if leg["stop_cid"] or leg["tp_cid"]:
                self.store.update(leg_id, stop_cid=None, tp_cid=None)          # re-placed by the next reconcile
            return
        await self.finalize(leg_id, r.avg_price, why)

    async def finalize(self, leg_id: int, exit_px: float, why: str) -> None:
        leg = self.store.leg(leg_id)
        q = float(leg["qty"])
        pnl = q * (exit_px - leg["entry"]) - COST["addon" if leg["parent"] else leg["strategy"]] * q * leg["entry"]
        R = pnl / (q * rules.R_ATR * leg["atr"])
        self.store.update(leg_id, state="CLOSED", exit=exit_px, exit_time=now_iso(), why=why, pnl_usd=pnl, R=R)
        self.store.event(leg_id, "close", f"{why} @ {exit_px} R={R:+.2f} ${pnl:+.2f}")
        await self.notify(f"{self.label(leg)} exit {leg['symbol'][:-4]} ({why}) @ {exit_px:.6g}: {R:+.2f}R (${pnl:+.2f})")

    async def reconcile(self, leg: dict) -> None:
        s, lid = leg["symbol"], leg["id"]
        if leg["state"] == "BID":
            r = await self.broker.query(s, leg["bid_kind"], leg["bid_cid"])
            if not r.ok:
                return
            if r.status == "FILLED" and r.filled_qty > 0:
                return await self.open_leg(lid, r.avg_price, r.filled_qty)
            if r.status == "PARTIALLY_FILLED":                              # take what filled; exits must match it
                r = await self.broker.cancel_bid(s, leg["bid_kind"], leg["bid_cid"])
                if r.filled_qty > 0:
                    return await self.open_leg(lid, r.avg_price, r.filled_qty)
            if r.status in ("CANCELED", "EXPIRED", "REJECTED") and r.filled_qty <= 0:
                self.store.update(lid, state="CANCELLED", why=f"bid {r.status.lower()}")
                if r.status == "REJECTED":
                    await self.notify(f"{s[:-4]} triggered bid rejected ({r.msg})", key=f"bidrej:{s}", every_s=3600)
            return
        if leg["state"] != "OPEN":
            return
        for cid, why in ((leg["stop_cid"], "stop"), (leg["tp_cid"], "tp")):
            if not cid:
                continue
            r = await self.broker.query(s, "algo", cid)
            if r.ok and r.filled_qty > 0 and r.status in ("FILLED", "TRIGGERED", "FINISHED"):
                other = leg["tp_cid"] if why == "stop" else leg["stop_cid"]
                if other:
                    await self.broker.cancel(s, "algo", other)
                return await self.finalize(lid, r.avg_price, why)
            if r.ok and r.status in ("CANCELED", "EXPIRED"):                 # exchange dropped an exit: put it back
                self.store.update(lid, **({"stop_cid": None} if why == "stop" else {"tp_cid": None}))
                self.store.event(lid, "warn", f"{why} order {r.status}; re-placing")
        leg = self.store.leg(lid)
        if leg["state"] == "OPEN" and ((not leg["stop_cid"] and leg["stop"] > 0) or (leg["strategy"] == "wick" and not leg["tp_cid"])):
            await self.place_exits(lid)

    async def end_bid(self, leg: dict) -> None:
        r = await self.broker.cancel_bid(leg["symbol"], leg["bid_kind"], leg["bid_cid"])
        if r.filled_qty > 0:
            return await self.open_leg(leg["id"], r.avg_price, r.filled_qty)
        self.store.db.execute("DELETE FROM legs WHERE id=?", (leg["id"],))   # unfilled hourly bids leave no row

    # ------------------------------------------------------------------------------------------- schedule
    async def on_hour(self, hour: datetime) -> None:
        await self.refresh_atr()
        for leg in self.store.live_legs():
            if leg["strategy"] == "wick" and leg["state"] == "BID":
                await self.end_bid(leg)
        if not self.cfg["strategies"].get("wick"):
            return
        blocked = self.entries_blocked()
        if blocked:
            logger.info("wick bids skipped", reason=blocked)
            return
        placed, bumped = 0, []
        ladder = [float(k) for k in self.cfg.get("wick_ladder") or [rules.WICK_K]]
        btc_h = self.close_h.get("BTCUSDT")
        for s in self.syms:
            if self.cooling("wick", s) or s not in self.atr:
                continue
            f = self.filters[s]
            plan = []                                                      # (k, bid, qty, bumped)
            for k in ladder:
                L = rules.wick_bid(self.close_h[s], self.atr[s], k)
                if L is not None and L > 0:
                    bid = rules.floor_to(L, f.tick)
                    plan.append((k, bid, *rules.rung_qty(self.r_usd, 1 / len(ladder), self.atr[s], f.step, float(bid), f.min_notional)))
            if len(ladder) > 1 and any(p_[3] for p_ in plan):
                # a 1/3-R rung is under Binance's minimum order: this coin takes the v1 single 5-ATR bid at the full R
                # instead, so its risk stays ~r_usd per R (raising each rung to the minimum would be 2.4-4x the R)
                L = rules.wick_bid(self.close_h[s], self.atr[s], rules.WICK_K)
                plan = []
                if L is not None and L > 0:
                    bid = rules.floor_to(L, f.tick)
                    plan = [(rules.WICK_K, bid, *rules.rung_qty(self.r_usd, 1.0, self.atr[s], f.step, float(bid), f.min_notional))]
                    bumped.append(s[:-4])
            for k, bid, qty, bump in plan:
                if self.holding("wick", s, k) or qty <= 0:
                    continue
                lid = self.store.new_leg(strategy="wick", symbol=s, state="BID", qty=str(qty), atr=self.atr[s], r_usd=self.r_usd,
                                         bid=float(bid), ref=self.close_h[s], hour=hour.isoformat(), mode=self.mode, rung=k,
                                         expires=(hour + timedelta(hours=1)).isoformat(),
                                         signal={"close_h": self.close_h[s], "btc_close_h": btc_h, "bumped": bump})
                cid = self.cid(lid, "wick", "b")
                r = await self.broker.entry_bid(s, qty, bid, cid)
                if not r.ok:
                    self.store.update(lid, state="ERROR", why=f"bid rejected {r.code} {r.msg}")
                    await self.notify(f"{s[:-4]} bid rejected: {r.code} {r.msg}", key=f"bid:{s}:{r.code}", every_s=6 * 3600)
                    continue
                self.store.update(lid, bid_cid=cid, bid_kind=r.kind)
                placed += 1
                if r.filled_qty > 0 and r.status == "FILLED":
                    await self.open_leg(lid, r.avg_price, r.filled_qty)
        logger.info("wick bids placed", n=placed, hour=hour.isoformat(), single_bid_coins=bumped)

    async def on_5m(self, bar_close: datetime) -> None:
        for leg in self.store.live_legs():                                  # time exits at the first 5m open >= expiry
            if leg["state"] == "OPEN" and leg["expires"] and _ts(leg["expires"]) <= bar_close + timedelta(seconds=30):
                await self.close_market(leg["id"], "time")
        want = int((bar_close - timedelta(minutes=5)).timestamp() * 1000)   # open time of the bar that just closed
        btc = {b[0]: b[4] for b in await self.market.klines("BTCUSDT", "5m", 15)}
        await self.addons(bar_close, btc)
        if not self.cfg["strategies"].get("discount"):
            return
        thr = self.cfg.get("discount_btc_fall")
        if thr is not None and not rules.btc_falling(btc.get(want), btc.get(want - 3600000), float(thr)):
            return                                                           # v2: discount only while BTC is falling
        blocked = self.entries_blocked()
        todo = [s for s in self.syms if not (blocked or self.holding("discount", s) or self.cooling("discount", s) or s not in self.atr)]
        perp = await asyncio.gather(*[self.market.klines(s, "5m", 3) for s in todo])
        spot = await asyncio.gather(*[self.market.klines(s, "5m", 3, spot=True) for s in todo])
        for s, pk_, sk_ in zip(todo, perp, spot):
            p, sp = {b[0]: b[4] for b in pk_}, {b[0]: b[4] for b in sk_}
            if want not in p or want not in sp or want - 300000 not in p or want - 300000 not in sp:
                continue
            b_now, b_prev = p[want] / sp[want] - 1, p[want - 300000] / sp[want - 300000] - 1
            if not rules.disc_signal(b_prev, b_now):
                continue
            f = self.filters[s]
            qty = rules.qty_for_r(self.r_usd, self.atr[s], f.market_step)
            if qty <= 0 or float(qty) * p[want] < f.min_notional:
                logger.info("discount below min notional", symbol=s)
                continue
            lid = self.store.new_leg(strategy="discount", symbol=s, state="ENTERING", qty=str(qty), atr=self.atr[s],
                                     r_usd=self.r_usd, mode=self.mode, signal={"basis": b_now, "basis_prev": b_prev})
            r = await self.broker.market(s, "BUY", qty, False, self.cid(lid, "discount", "e"))
            if not r.ok or r.filled_qty <= 0:
                self.store.update(lid, state="ERROR", why=f"entry failed {r.code} {r.msg}")
                await self.notify(f"{s[:-4]} discount entry failed: {r.code} {r.msg}", key=f"disc:{s}", every_s=3600)
                continue
            self.store.update(lid, signal=json.dumps({"basis": b_now, "basis_prev": b_prev}))
            await self.open_leg(lid, r.avg_price, r.filled_qty)

    async def addons(self, bar_close: datetime, btc: dict) -> None:
        """v2 add-on: for rungs whose fill bar has closed, buy a second unit when BTC dumped >= 1.7% since the bid hour."""
        thr = self.cfg.get("wick_addon_btc_dump")
        for leg in self.store.live_legs():
            if leg["addon"] != "pending" or _ts(leg["addon_at"]) > bar_close:
                continue
            if leg["state"] != "OPEN" or thr is None:                        # exited inside its fill bar: no add-on (paper)
                self.store.update(leg["id"], addon="skipped")
                continue
            sig = json.loads(leg["signal"] or "{}")
            fb_open = int((_ts(leg["addon_at"]) - timedelta(minutes=5)).timestamp() * 1000)
            c_fill = btc.get(fb_open) or (btc[max(btc)] if btc else None)
            if not rules.btc_dump(sig.get("btc_close_h"), c_fill, float(thr)):
                self.store.update(leg["id"], addon="no")
                continue
            if self.entries_blocked():
                self.store.update(leg["id"], addon="blocked")
                continue
            s, q = leg["symbol"], Decimal(leg["qty"])
            lid = self.store.new_leg(strategy="wick", symbol=s, state="ENTERING", qty=leg["qty"], atr=leg["atr"], r_usd=leg["r_usd"],
                                     ref=leg["ref"], rung=leg["rung"], parent=leg["id"], hour=leg["hour"], mode=self.mode,
                                     signal={"btc_close_h": sig.get("btc_close_h"), "btc_fill_close": c_fill})
            r = await self.broker.market(s, "BUY", q, False, self.cid(lid, "wick", "a"))
            self.store.update(leg["id"], addon="done" if r.ok and r.filled_qty > 0 else "failed")
            if not r.ok or r.filled_qty <= 0:
                self.store.update(lid, state="ERROR", why=f"add-on failed {r.code} {r.msg}")
                await self.notify(f"{s[:-4]} add-on failed: {r.code} {r.msg}", key=f"addon:{s}", every_s=3600)
                continue
            await self.open_leg(lid, r.avg_price, r.filled_qty, expires=leg["expires"])   # exits with its rung

    async def on_minute(self) -> None:
        if self.mode == "dry":                                             # feed closed 1m bars to the simulator
            for s in self.broker.symbols_with_orders():
                bars = await self.market.klines(s, "1m", 3)
                for b in bars:
                    if b[0] > self.fed_1m.get(s, 0):
                        self.fed_1m[s] = b[0]
                        if self.broker.on_bar(s, *b[:5]):
                            self.dirty.add(s)
        else:
            for leg in self.store.live_legs():
                await self.reconcile(leg)
        st = {"t": now_iso(), "mode": self.mode, "paused": self.entries_blocked(),
              "legs": [{k: x[k] for k in ("id", "strategy", "rung", "parent", "symbol", "state", "entry", "stop", "qty")} for x in self.store.live_legs()]}
        with open(self.cfg["status_file"] + ".tmp", "w") as fh:
            json.dump(st, fh)
        os.replace(self.cfg["status_file"] + ".tmp", self.cfg["status_file"])

    async def guards(self) -> None:
        if self.mode == "dry" or not self.cfg.get("equity_floor_usd"):
            return
        eq = await self.broker.equity()
        if eq is None:
            return
        floor = float(self.cfg["equity_floor_usd"])
        if eq < floor and not self.paused_reason:
            self.paused_reason = f"wallet balance ${eq:.2f} < floor ${floor:.2f}"
            await self.notify(f"PAUSED new entries: {self.paused_reason}. Open legs keep their exits.")
        elif self.paused_reason and eq >= floor * 1.1:
            self.paused_reason = None
            await self.notify(f"entries resumed: wallet balance ${eq:.2f}")
        for s in {x["symbol"] for x in self.store.live_legs() if x["state"] == "OPEN"}:   # position sanity
            amt = await self.broker.position_amt(s)
            exp = sum(float(x["qty"]) for x in self.store.live_legs() if x["symbol"] == s and x["state"] == "OPEN")
            if amt is not None and abs(amt - exp) > 1e-9 * max(1.0, exp):
                self.dirty.add(s)
                await self.notify(f"{s[:-4]} position {amt} vs engine legs {exp}: reconciling", key=f"pos:{s}", every_s=3600)

    async def daily(self) -> None:
        y = (datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds")
        cl = self.store.closed_since(y)
        op = [x for x in self.store.live_legs() if x["state"] == "OPEN"]
        if not cl and not op:
            return
        by = {k: [x for x in cl if x["strategy"] == k] for k in ("wick", "discount")}
        parts = [f"{k} {len(v)} closed {sum(x['R'] for x in v):+.2f}R (${sum(x['pnl_usd'] for x in v):+.2f})" for k, v in by.items() if v]
        await self.notify("last 24h: " + (", ".join(parts) if parts else "no exits") + f"; open {len(op)}")

    # ------------------------------------------------------------------------------------------- user stream (testnet/live)
    async def user_stream(self) -> None:
        base = "wss://stream.binancefuture.com/ws/" if self.mode == "testnet" else "wss://fstream.binance.com/ws/"

        def syms_in(x) -> set:
            out = set()
            if isinstance(x, dict):
                for k, v in x.items():
                    if k == "s" and isinstance(v, str):
                        out.add(v)
                    else:
                        out |= syms_in(v)
            elif isinstance(x, list):
                for v in x:
                    out |= syms_in(v)
            return out

        while not self.stop:
            try:
                key = await self.broker.listen_key()
                if not key:
                    await asyncio.sleep(30)
                    continue
                async with aiohttp.ClientSession() as sess, sess.ws_connect(base + key, heartbeat=60) as ws:
                    logger.info("user stream connected")
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            self.dirty |= syms_in(json.loads(msg.data)) & set(self.syms)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
            except Exception as e:
                logger.warning("user stream error", error=type(e).__name__)
            await asyncio.sleep(5)

    async def keepalive(self) -> None:
        while not self.stop:
            await asyncio.sleep(1800)
            try:
                await self.broker.keepalive()
            except Exception as e:
                logger.warning("listen key keepalive failed", error=type(e).__name__)

    # ------------------------------------------------------------------------------------------- main
    async def startup(self) -> None:
        await self.market.start()
        await self.broker.start()
        self.filters = await self.market.filters(self.syms)
        missing = [s for s in self.syms if s not in self.filters]
        if missing:
            logger.error("no exchange filters", symbols=missing)
            raise SystemExit(SETUP_EXIT)
        problems = await self.broker.preflight(self.syms, int(self.cfg["leverage"]), self.cfg["margin_type"])
        if problems:
            await self.notify("NOT STARTED: " + "; ".join(problems)[:600])
            logger.error("preflight failed", problems=problems)
            raise SystemExit(SETUP_EXIT)
        await self.refresh_atr()
        for leg in self.store.live_legs():
            if self.mode == "dry":                                       # simulated orders do not survive a restart
                if leg["state"] == "BID":
                    self.store.db.execute("DELETE FROM legs WHERE id=?", (leg["id"],))
                elif leg["state"] == "OPEN":
                    self.store.update(leg["id"], stop_cid=None, tp_cid=None)
                    await self.place_exits(leg["id"])
                elif leg["state"] == "ENTERING":
                    self.store.update(leg["id"], state="ERROR", why="restart during entry")
            elif leg["state"] == "BID" and _ts(leg["expires"]) <= datetime.now(UTC):
                await self.end_bid(leg)
            else:
                await self.reconcile(leg)
        live = self.store.live_legs()
        await self.notify(f"started ({self.mode}); ${self.r_usd:g}/R, stop -{self.stop_r:g}R, "
                          f"strategies {[k for k, v in self.cfg['strategies'].items() if v]}, ladder {self.cfg.get('wick_ladder')}, "
                          f"add-on {self.cfg.get('wick_addon_btc_dump')}, discount BTC filter {self.cfg.get('discount_btc_fall')}, "
                          f"{len(live)} live legs")

    async def run(self) -> None:
        await self.startup()
        tasks = [] if self.mode == "dry" else [asyncio.create_task(self.user_stream()), asyncio.create_task(self.keepalive())]
        now = datetime.now(UTC)
        last_h = now.replace(minute=0, second=0, microsecond=0)               # first bids at the next full hour
        last_5 = last_1 = last_g = last_d = None
        while not self.stop:
            now = datetime.now(UTC)
            h = now.replace(minute=0, second=0, microsecond=0)
            if h != last_h and now.second >= 5:
                last_h = h
                await self.safe(self.on_hour(h), "hourly")
            m5 = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
            if m5 != last_5 and now.second >= 6 and last_h == h:
                last_5 = m5
                await self.safe(self.on_5m(m5), "5m")
            m1 = now.replace(second=0, microsecond=0)
            if m1 != last_1 and now.second >= 3:
                last_1 = m1
                await self.safe(self.on_minute(), "minute")
            if last_g is None or (now - last_g).total_seconds() >= 300:
                last_g = now
                await self.safe(self.guards(), "guards")
            if now.hour == 0 and now.minute == 5 and last_d != now.date():
                last_d = now.date()
                await self.safe(self.daily(), "daily")
            if self.dirty:
                ds, self.dirty = self.dirty, set()
                for leg in self.store.live_legs():
                    if leg["symbol"] in ds:
                        await self.safe(self.reconcile(leg), "reconcile")
            await asyncio.sleep(0.5)
        for t in tasks:
            t.cancel()
        await self.broker.close()
        await self.market.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="wick catcher + perp discount engine")
    ap.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "config.yaml"))
    a = ap.parse_args()
    cfg = yaml.safe_load(open(a.config))
    setup_logging(level=cfg.get("log_level", "INFO"), log_file=cfg["log_file"])
    eng = Engine(cfg)
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: setattr(eng, "stop", True))
    loop.run_until_complete(eng.run())


if __name__ == "__main__":
    main()
