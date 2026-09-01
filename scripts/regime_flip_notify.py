#!/root/bitana/venv/bin/python3
"""BTC regime-flip Telegram watchdog.

Runs every 15 min via Hermes cron (no_agent). Fetches BTC 4h klines, computes the
regime EXACTLY like the live selector (engines/btc_regime.compute_regime_snapshot:
4h EMA200 + ADX14>25, prices the live/incomplete bar), compares to the last
notified state, and prints an alert to stdout ONLY on a confirmed flip (2
consecutive readings agree = 30-min persistence debounce). Hermes delivers
stdout to Telegram; silence = no flip.

State: /root/bitana/storage/regime_state.json
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, "/root/bitana")
from core.models import Candle  # noqa: E402
from engines.btc_regime import compute_regime_snapshot  # noqa: E402

STATE = "/root/bitana/storage/regime_state.json"
KLINES_URL = ("https://fapi.binance.com/fapi/v1/klines"
              "?symbol=BTCUSDT&interval=4h&limit=260")


def load_arms() -> dict[str, frozenset[str]]:
    """Per-arm allowed regimes from the LIVE yaml (2026-09-01).

    Replaces the hardcoded ARMS dict — it carried asia {neutral,bear} after
    the live rule moved to ["neutral"] (session-level override), so a bear
    flip would have printed asia ON while the live engine has it dark. Same
    drift class as the WLA mirror constants fixed 08-31. Resolves through the
    typed loader with the engine's fallback semantics (session override, else
    global allowed_btc_regimes).
    """
    from config.loader import load_config

    key = os.environ.pop("API_FOOTBALL_KEY", None)  # pydantic chokes on it
    try:
        cfg = load_config("/root/bitana/config/live_burst_ny_asia.yaml")
    finally:
        if key is not None:
            os.environ["API_FOOTBALL_KEY"] = key
    bf = cfg.burst_follow
    out: dict[str, frozenset[str]] = {}
    for arm, rule in bf.session_rules.items():
        name = rule.shadow_strategy or arm
        if name in out:
            raise RuntimeError(f"two session arms share shadow_strategy {name!r}")
        out[name] = frozenset(rule.allowed_btc_regimes or bf.allowed_btc_regimes)
    return out


def fetch_candles() -> list[Candle]:
    with urllib.request.urlopen(KLINES_URL, timeout=20) as resp:
        raw = json.loads(resp.read())
    out = []
    for k in raw:
        out.append(Candle(
            symbol="BTCUSDT", timeframe="4h",
            open_time=datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
            close_time=datetime.fromtimestamp(k[6] / 1000, tz=timezone.utc),
            open=float(k[1]), high=float(k[2]), low=float(k[3]), close=float(k[4]),
            volume=float(k[5]), is_closed=bool(k[6] < datetime.now(timezone.utc).timestamp() * 1000),
        ))
    return out


def load_state() -> dict:
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def main() -> None:
    snap = compute_regime_snapshot(fetch_candles())
    if not snap.state:
        return  # insufficient history — stay silent
    st = load_state()
    notified = st.get("notified")
    last_raw = st.get("last_raw")
    bar = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")

    if snap.state != last_raw:
        # first disagreement with previous reading — wait one tick (persistence)
        with open(STATE, "w") as f:
            json.dump({"notified": notified, "last_raw": snap.state,
                       "since": st.get("since") if notified == snap.state else bar}, f)
        return
    if snap.state == notified:
        return  # no confirmed change — silent

    arms = "\n".join(
        f"{'✅ ON' if snap.state in regs else '⛔ off'}  {name} ({'/'.join(sorted(regs))})"
        for name, regs in load_arms().items()
    )
    print(f"🔔 REGIME FLIP: {notified} → {snap.state}\n"
          f"4h EMA200 dist {snap.distance_from_ema_pct:+.2f}% · ADX {snap.adx:.1f} · read {bar}\n"
          f"Live arms in {snap.state}:\n{arms}")
    with open(STATE, "w") as f:
        json.dump({"notified": snap.state, "last_raw": snap.state, "since": bar}, f)


if __name__ == "__main__":
    main()
