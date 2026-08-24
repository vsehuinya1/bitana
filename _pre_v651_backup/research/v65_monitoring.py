"""v6.5-revert live monitoring — session reports, shadow filters, promotion/kill rules."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

STRATEGY_VERSION = "v65_revert"

# OOS backtest anchors (Jan–May 2026, 28 symbols, NY 14–22 UTC)
EXPECTED_TRADES_PER_SESSION = 2.63
EXPECTED_R_PER_SESSION = 0.469

WEAK_SYMBOLS = frozenset({
    "NEARUSDT", "FILUSDT", "ETHUSDT", "UNIUSDT", "RENDERUSDT",
})

CORE_HOURS_16_18 = frozenset({16, 17, 18})
NY_SESSION_HOURS = frozenset(range(14, 22))
# Asia paper-shadow: OOS +0.44R on proven-28; exclude weak hours 02, 07 UTC
ASIA_SHADOW_HOURS = frozenset({0, 1, 3, 4, 5, 6})
EXPECTED_ASIA_TRADES_PER_SESSION = 2.0
EXPECTED_ASIA_R_PER_SESSION = 0.376

PROMOTE_MIN_N = 50
PROMOTE_MIN_AVG_R = 0.0
WARN_FIRST_N = 25
WARN_FIRST_MIN_R = -10.0
KILL_MIN_N = 50
KILL_MAX_AVG_R = -0.10


def parse_entry_hour(entry_time: str) -> int:
    try:
        dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.hour
    except (ValueError, TypeError):
        return -1


def evaluate_entry_shadow_filters(
    *,
    symbol: str,
    decile: int,
    entry_hour: int,
    confirmations: dict,
) -> dict[str, bool]:
    """Hypothetical entry filters — telemetry only, never blocks live."""
    imb = bool(confirmations.get("imb"))
    vol = bool(confirmations.get("vol"))
    return {
        "live_v65": True,
        "exclude_d1": decile != 1,
        "exclude_weak_symbols": symbol not in WEAK_SYMBOLS,
        "core_hours_16_18": entry_hour in CORE_HOURS_16_18,
        "d1_strict_imb_and_vol": decile != 1 or (imb and vol),
    }


def session_trades(trades: list[dict], session_date: str) -> list[dict]:
    """Closed trades with entry in NY window on session_date (UTC)."""
    out = []
    for t in trades:
        et = t.get("entry_time", "")
        if not et.startswith(session_date):
            continue
        h = parse_entry_hour(et)
        if h in NY_SESSION_HOURS:
            out.append(t)
    return out


def asia_session_trades(trades: list[dict], session_date: str) -> list[dict]:
    """Closed shadow trades with Asia-window entries on session_date (UTC)."""
    out = []
    for t in trades:
        et = t.get("entry_time", "")
        if not et.startswith(session_date):
            continue
        if parse_entry_hour(et) in ASIA_SHADOW_HOURS:
            out.append(t)
    return out


def build_asia_shadow_report(
    trades: list[dict],
    session_date: str,
    cumulative_r: float,
    open_positions: list[dict],
) -> str:
    """Session report for Asia shadow trades (0–6 UTC entries on session_date)."""
    sess = asia_session_trades(trades, session_date)
    n = len(sess)
    total_r = sum(float(t.get("pnl_r", 0)) for t in sess)
    wins = sum(1 for t in sess if float(t.get("pnl_r", 0)) > 0)
    wr = (wins / n * 100) if n else 0.0

    lines = [
        f"🌏 ASIA SHADOW CLOSE — {session_date}",
        "",
        f"Shadow trades: {n} (exp {EXPECTED_ASIA_TRADES_PER_SESSION:.1f}, "
        f"Δ{n - EXPECTED_ASIA_TRADES_PER_SESSION:+.1f})",
        f"Session R: {total_r:+.2f} (exp {EXPECTED_ASIA_R_PER_SESSION:+.2f}, "
        f"Δ{total_r - EXPECTED_ASIA_R_PER_SESSION:+.2f})",
        f"WR: {wr:.0f}% | Cumulative shadow R: {cumulative_r:+.2f}",
        f"Open shadow positions: {len(open_positions)}",
    ]
    if sess:
        decile_stats: dict[int, dict[str, float]] = defaultdict(lambda: {"n": 0, "r": 0.0})
        for t in sess:
            d = int(t.get("decile") or 0)
            decile_stats[d]["n"] += 1
            decile_stats[d]["r"] += float(t.get("pnl_r", 0))
        lines.append("\nBy decile:")
        for d in sorted(decile_stats):
            st = decile_stats[d]
            lines.append(f"  D{d}: {int(st['n'])}t {st['r']:+.2f}R")
    return "\n".join(lines)


def build_session_report(
    trades: list[dict],
    session_date: str,
    equity: float,
    open_positions: list[dict],
) -> str:
    sess = session_trades(trades, session_date)
    n = len(sess)
    total_r = sum(float(t.get("pnl_r", 0)) for t in sess)
    wins = sum(1 for t in sess if float(t.get("pnl_r", 0)) > 0)
    wr = (wins / n * 100) if n else 0.0
    delta_r = total_r - EXPECTED_R_PER_SESSION
    delta_n = n - EXPECTED_TRADES_PER_SESSION

    decile_stats: dict[int, dict[str, float]] = defaultdict(lambda: {"n": 0, "r": 0.0})
    sym_stats: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "r": 0.0})
    reason_stats: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "r": 0.0})
    for t in sess:
        d = int(t.get("decile") or 0)
        decile_stats[d]["n"] += 1
        decile_stats[d]["r"] += float(t.get("pnl_r", 0))
        sym = t.get("symbol", "?")
        sym_stats[sym]["n"] += 1
        sym_stats[sym]["r"] += float(t.get("pnl_r", 0))
        reason = t.get("exit_reason", "?")
        reason_stats[reason]["n"] += 1
        reason_stats[reason]["r"] += float(t.get("pnl_r", 0))

    lines = [
        f"🌆 NY SESSION CLOSE — {session_date} ({STRATEGY_VERSION})",
        "",
        f"Trades: {n} (exp {EXPECTED_TRADES_PER_SESSION:.1f}, Δ{n - EXPECTED_TRADES_PER_SESSION:+.1f})",
        f"Realized R: {total_r:+.2f} (exp {EXPECTED_R_PER_SESSION:+.2f}, Δ{delta_r:+.2f})",
        f"WR: {wr:.0f}% | Equity: ${equity:.2f}",
        f"Open positions: {len(open_positions)}",
    ]

    if decile_stats:
        lines.append("\nBy decile:")
        for d in sorted(decile_stats):
            st = decile_stats[d]
            lines.append(f"  D{d}: {int(st['n'])}t {st['r']:+.2f}R")

    if reason_stats:
        lines.append("\nBy exit:")
        for reason, st in sorted(reason_stats.items(), key=lambda x: abs(x[1]["r"]), reverse=True):
            lines.append(f"  {reason}: {int(st['n'])}t {st['r']:+.2f}R")

    if sym_stats:
        lines.append("\nBy symbol:")
        for sym, st in sorted(sym_stats.items(), key=lambda x: abs(x[1]["r"]), reverse=True):
            lines.append(f"  {sym}: {int(st['n'])}t {st['r']:+.2f}R")

    if open_positions:
        lines.append("\nStill open:")
        for p in open_positions:
            lines.append(
                f"  {p.get('symbol')} D{p.get('decile', '?')} "
                f"@ {float(p.get('entry_price', 0)):.6f} ({p.get('candles_held', 0)} bars)"
            )

    return "\n".join(lines)


def evaluate_promotion_status(trades: list[dict]) -> dict[str, Any]:
    """Promotion/kill rules on strategy-version trades (newest first by exit_time)."""
    cohort = [
        t for t in trades
        if t.get("strategy_version") == STRATEGY_VERSION or not t.get("strategy_version")
    ]
    # Prefer version-stamped only once we have them
    stamped = [t for t in trades if t.get("strategy_version") == STRATEGY_VERSION]
    if stamped:
        cohort = stamped

    n = len(cohort)
    if n == 0:
        return {"status": "collecting", "n": 0, "avg_r": 0.0, "message": "No v65 trades yet"}

    rs = [float(t.get("pnl_r", 0)) for t in cohort]
    total_r = sum(rs)
    avg_r = total_r / n

    stop_r = sum(r for t, r in zip(cohort, rs) if t.get("exit_reason") == "stop_loss")
    trail_r = sum(r for t, r in zip(cohort, rs) if t.get("exit_reason") == "vol_trail")
    stop_n = sum(1 for t in cohort if t.get("exit_reason") == "stop_loss")
    trail_n = sum(1 for t in cohort if t.get("exit_reason") == "vol_trail")

    result: dict[str, Any] = {
        "status": "collecting",
        "n": n,
        "total_r": round(total_r, 2),
        "avg_r": round(avg_r, 3),
        "stop_n": stop_n,
        "stop_r": round(stop_r, 2),
        "trail_n": trail_n,
        "trail_r": round(trail_r, 2),
        "message": "",
        "halt_entries": False,
        "alert_tier": "info",
    }

    if n >= WARN_FIRST_N and total_r <= WARN_FIRST_MIN_R:
        result["status"] = "warning"
        result["alert_tier"] = "warning"
        result["message"] = (
            f"⚠️ First {n} v65 trades at {total_r:+.1f}R (warn threshold {WARN_FIRST_MIN_R:+.0f}R)"
        )

    if n >= KILL_MIN_N:
        if avg_r < KILL_MAX_AVG_R:
            result["status"] = "kill"
            result["halt_entries"] = True
            result["alert_tier"] = "critical"
            result["message"] = (
                f"🛑 KILL: {n} trades avg {avg_r:+.3f}R (< {KILL_MAX_AVG_R:+.2f}R) — entries halted"
            )
        elif stop_n >= 20 and trail_r <= 0 and abs(stop_r) > abs(trail_r):
            result["status"] = "kill"
            result["halt_entries"] = True
            result["alert_tier"] = "critical"
            result["message"] = (
                f"🛑 KILL: stop_loss {stop_r:+.1f}R dominates without vol_trail offset "
                f"(trail {trail_r:+.1f}R) — entries halted"
            )
        elif avg_r > PROMOTE_MIN_AVG_R:
            result["status"] = "promote"
            result["alert_tier"] = "info"
            result["message"] = (
                f"✅ PROMOTE candidate: {n} trades avg {avg_r:+.3f}R "
                f"(vol_trail {trail_r:+.1f}R / stop {stop_r:+.1f}R)"
            )
        else:
            result["status"] = "marginal"
            result["message"] = f"Marginal: {n} trades avg {avg_r:+.3f}R — keep collecting"

    return result
