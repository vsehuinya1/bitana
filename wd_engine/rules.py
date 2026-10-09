"""Entry/exit maths of PREREG-WICK-CATCHER and PREREG-PERP-DISCOUNT as the live engine applies them.

Mirror of research/wick_catcher_reader.py (k=5 primary book) and research/perp_discount_reader.py (4h primary book);
tests/test_wd_engine_rules.py replays both readers on cached klines and checks these functions give the same levels.
Pure functions only: no I/O, no exchange calls.

Sizing: 1R = 3 x ATR1h (the paper tracks' reporting unit). A leg risks `r_usd` per R, so qty = r_usd / (3 x ATR1h)
(price-independent: $ per R = qty x 3 x ATR). The disaster stop sits k R below the entry: entry - k x 3 x ATR1h.
"""
from __future__ import annotations

import math
import statistics
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

ATR_N = 14
R_ATR = 3.0               # 1R = 3 x ATR1h
WICK_K = 5.0              # bid at close(H) - 5 x ATR1h
WICK_STRICT = 0.1         # paper fill rule: low <= L - 0.1 ATR (live fills on touch; logged for the paper/live gap)
WICK_MIN_ATR = 0.001      # skip when ATR1h / close < 0.1%
WICK_HOLD_H = 24
WICK_LADDER = (5.0, 6.5, 8.0)   # v2 (2026-10-04): three rungs, 1/3 R each (edge report "Improving the wick/discount engine")
BTC_DUMP = -0.017         # v2 add-on: BTC fill-bar close vs the bid-hour close <= -1.7% -> second unit per rung fill
DISC_THR = -0.003         # perp/spot - 1 crosses below -30 bps
DISC_HOLD_H = 4
DISC_BTC_FALL = -0.01     # v2 filter: discount entries only when BTC's 5m close <= -1% vs 60 min earlier
MW_Z, MW_SHARE, MW_MIN_VALID, MW_WINDOW_H, MW_MIN_H = -2.0, 0.5, 16, 720, 200   # market-wide selloff hour (2026-10-09)


def atr1h(bars: list[tuple[float, float, float, float]]) -> float | None:
    """Mean true range of the last 14 closed hourly bars (o, h, l, c), oldest first; needs >= 15 bars.
    Same as the readers: TR = max(h, prev c) - min(l, prev c), rolling 14 mean ending at the last bar."""
    if len(bars) < ATR_N + 1:
        return None
    trs = [max(b[1], p[3]) - min(b[2], p[3]) for p, b in zip(bars[:-1], bars[1:])]
    return sum(trs[-ATR_N:]) / ATR_N


def wick_bid(close_h: float, atr: float | None, k: float = WICK_K) -> float | None:
    """Bid level k x ATR1h under the last hourly close, or None when the coin is too quiet (ATR1h < 0.1% of price)."""
    if not atr or atr / close_h < WICK_MIN_ATR:
        return None
    return close_h - k * atr


def btc_dump(btc_close_h: float | None, btc_close_fill: float | None, thr: float = BTC_DUMP) -> bool:
    """BTC fell >= |thr| from the bid-hour close to the fill-bar close (the add-on condition)."""
    return bool(btc_close_h and btc_close_fill and btc_close_fill / btc_close_h - 1 <= thr)


def btc_falling(c_now: float | None, c_60m: float | None, thr: float = DISC_BTC_FALL) -> bool:
    """BTC's 5m close at the signal bar vs the close 60 min earlier <= thr (the discount filter)."""
    return bool(c_now and c_60m and c_now / c_60m - 1 <= thr)


def market_wide(closes: dict[str, list[float]], z: float = MW_Z, share: float = MW_SHARE, min_valid: int = MW_MIN_VALID,
                window: int = MW_WINDOW_H, min_h: int = MW_MIN_H) -> tuple[bool, int, int]:
    """Was the hour that just closed a market-wide selloff hour? (research tag of record: research_cache/edge/
    improve_wd.py MKT, used by reports/wd_sizing_prereg.md). Per coin: the last hourly log return divided by the stdev
    (ddof 1) of the up-to-`window` hourly log returns before it (needs >= min_h of them); the hour is market-wide when
    >= share of the valid coins have z <= `z` and >= min_valid coins are valid.
    closes: per coin, hourly closes oldest first, ending with the hour that just closed. Returns (flag, n_down, n_valid)."""
    n_down = n_valid = 0
    for c in closes.values():
        lr = [math.log(b / a) for a, b in zip(c[:-1], c[1:]) if a > 0 and b > 0]
        prev = lr[-1 - window:-1]
        if len(prev) < max(min_h, 2):
            continue
        sd = statistics.stdev(prev)
        if not sd > 0:
            continue
        n_valid += 1
        n_down += lr[-1] / sd <= z
    return n_valid >= min_valid and n_down >= share * n_valid, n_down, n_valid


def rung_qty(r_usd: float, weight: float, atr: float, step: str, price: float, min_notional: float) -> tuple[Decimal, bool]:
    """Quantity for weight x r_usd per R; if that is below the exchange's minimum order value, the minimum
    (rounded up to the step) instead. Returns (qty, bumped)."""
    q = floor_to(Decimal(str(r_usd * weight / (R_ATR * atr))), step)
    if float(q) * price >= min_notional:
        return q, False
    return ceil_to(Decimal(str(min_notional / price)), step), True


def disc_signal(basis_prev: float | None, basis_now: float | None) -> bool:
    """Perp/spot basis (perp close / spot close - 1, same 5m bar) crosses below -30 bps on this bar."""
    return basis_prev is not None and basis_now is not None and basis_now <= DISC_THR < basis_prev


def qty_for_r(r_usd: float, atr: float, step: str) -> Decimal:
    """Quantity so that 1R (3 x ATR1h) = r_usd, floored to the lot step."""
    return floor_to(Decimal(str(r_usd / (R_ATR * atr))), step)


def stop_price(entry: float, atr: float, k_r: float) -> float:
    return entry - k_r * R_ATR * atr


def floor_to(x: Decimal | float, step: str) -> Decimal:
    s = Decimal(step)
    return (Decimal(str(x)) / s).to_integral_value(rounding=ROUND_FLOOR) * s


def ceil_to(x: Decimal | float, step: str) -> Decimal:
    s = Decimal(step)
    return (Decimal(str(x)) / s).to_integral_value(rounding=ROUND_CEILING) * s


def fmt_dec(x: Decimal) -> str:
    """Plain decimal string for the API (no exponent, no trailing zeros)."""
    s = format(x.normalize(), 'f')
    return s if s not in ('-0', '') else '0'
