#!/root/bitana/venv/bin/python3
"""BTC regime-flip Telegram watchdog — RELAY mode (Sep-12).

v2 change: the script no longer recomputes regime independently. Root cause of
the Sep-11/12 "2/4 notifications + phantom flips": the old version applied its
own ADXBAND deadband + 2-tick persistence on a 15m cadence, while the live bot
applies its own hysteresis on a 3600s refresh — the two disagreed at the
boundary (script fired neutral at 00:15Z Sep-12; bot held bull until 04:01Z)
and produced flip counts that don't match the bot log (3 real transitions in
5 days, not 4).

Now: parse the bot's own "BTC regime updated" log line (single source of
truth) and notify exactly on its state changes. When the ADXBAND bot-side
patch (refresh 3600->300s, PREREG-ADXBAND row) ships, this relay needs no
change. State: /root/bitana/storage/regime_state.json
"""
import json
import os
import sys

sys.path.insert(0, "/root/bitana")

STATE = "/root/bitana/storage/regime_state.json"
LOG = "/root/bitana/logs/bitana-live-burst.log"


def last_bot_state() -> str | None:
    st = None
    try:
        with open(LOG, errors="replace") as f:
            for line in f:
                if '"event": "BTC regime updated"' in line and '"state"' in line:
                    i = line.index("{")
                    st = json.loads(line[i:])["state"]
    except FileNotFoundError:
        return None
    return st


def load_arms() -> dict[str, frozenset[str]]:
    """Per-arm allowed regimes from the LIVE yaml (single parse, engine fallbacks)."""
    from config.loader import load_config

    key = os.environ.pop("API_FOOTBALL_KEY", None)
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


def main() -> None:
    st = last_bot_state()
    if not st:
        return  # no log / no regime line yet — silent
    try:
        with open(STATE) as f:
            prev = json.load(f).get("notified")
    except Exception:
        prev = None
    if prev == st:
        return  # no change — silent
    try:
        with open(LOG, errors="replace") as f:
            for line in f:
                if '"event": "BTC regime updated"' in line:
                    o = json.loads(line[line.index("{"):])
    except Exception:
        o = {}
    arms = "\n".join(
        f"{'✅ ON' if st in regs else '⛔ off'}  {name} ({'/'.join(sorted(regs))})"
        for name, regs in load_arms().items()
    )
    bar = o.get("timestamp", "?")[:19]
    print(f"🔔 REGIME FLIP: {prev} → {st}\n"
          f"4h EMA200 dist {o.get('dist_pct', 0):+.2f}% · age {o.get('age_bars', '?')} bars · bot read {bar}Z\n"
          f"Live arms in {st}:\n{arms}")
    with open(STATE, "w") as f:
        json.dump({"notified": st, "since": bar}, f)


if __name__ == "__main__":
    main()
