#!/usr/bin/env python3
"""Bitana risk watch -> Telegram (read-only; 2026-09-25, owner order "Build the version that'll message on telegram").

Every 60s it reads the local dashboard API (token parsed from .env.dashboard) and Binance public BTC klines,
evaluates the triggers below, appends each alert to logs/risk_watch_alerts.log, and sends it to the owner's
Telegram chat with the bot's token. TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are parsed from .env; nothing else in
.env is read. It never touches the bots, config, databases or the exchange account. Each alert fires once per key.
  BRIEF     08:40 / 13:40 UTC Mon-Fri: regime, ADX slope, state at the next 4h close if BTC holds, LON-BULL-FADE,
            drawdown vs the equity pause, day R, open book, armed hours
  PRE-HOUR  ~5 min before an armed hour, only when a risk flag is up
  TAPE      BTC 60m <= -0.8% or 15m <= -0.5% (against the arms' side) while an arm is armed or legs are open
  BOOK      one arm's open legs: >= 3, all red and summed <= -0.5R, or summed unrealized <= -0.8R
  DAY       an arm's day R crosses -1 / -2 / -3R, or 3 straight losers
  DRAWDOWN  drawdown crosses 15 / 20 / 25 / 30%, or the equity pause comes within 1R
  STOPS     >= 2 stop-loss exits within 10 minutes
  REGIME    state change; provisional flip at the next 4h close (forming bar, last 60 min before the close)
  OPS       dashboard/bot unreachable, unit/pm2 down, stale feed, bot paused, reduced mode, critical task unhealthy
  EOD       21:05 UTC Mon-Fri: day summary
Usage: venv/bin/python -u ops/risk_watch.py [--dry] [--once] [--test]
  --dry   print instead of sending   --once  one START summary, then exit   --test  send a delivery test, exit
Service: deploy/bitana-risk-watch.service
"""
import json
import os
import ssl
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = '/root/bitana'
sys.path.insert(0, ROOT)
os.environ.pop('API_FOOTBALL_KEY', None)
from core.models import Candle  # noqa: E402
from engines.btc_regime import compute_regime_snapshot  # noqa: E402

LOG = f'{ROOT}/logs/risk_watch_alerts.log'
STATE = f'{ROOT}/logs/risk_watch_state.json'
API = 'http://127.0.0.1:8080/api/dashboard'
CTX = ssl.create_default_context()
EXIT_ADX, ENTER_ADX = 24.5, 25.5
FRESH_LIMIT = {'shadow writer': 900, 'force-order feed': 900, 'live bot log': 180, 'paper log': 600}
DD_LEVELS = (0.15, 0.20, 0.25, 0.30)
MAX_MSGS_PER_HOUR = 20
DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
DRY = '--dry' in sys.argv
# Row 11 (PREREG-LON-BULL-FADE) live-real basis at registration, quoted in fade-day messages
FADE_NOTE = 'Row 11 fade day: London lost on 3 of 4 such days live (-2.39R). Stand-down: /pause before 10:00, /resume before 14:00.'


def _env(path, key):
    try:
        for ln in open(path):
            ln = ln.strip().removeprefix('export ')
            if ln.startswith(key + '='):
                return ln.split('=', 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def _age_caps():
    """session -> max_regime_age_bars from the live yaml (read-only), e.g. PREREG-LON-AGE-CAP london: 17."""
    try:
        import yaml
        cfg = yaml.safe_load(open(f'{ROOT}/config/live_burst_ny_asia.yaml')) or {}
        rules = (cfg.get('burst_follow') or {}).get('session_rules') or {}
        return {k: v['max_regime_age_bars'] for k, v in rules.items()
                if isinstance(v, dict) and v.get('max_regime_age_bars') is not None}
    except Exception:
        return {}


AGE_CAPS = _age_caps()
DASH_TOKEN = _env(f'{ROOT}/.env.dashboard', 'DASHBOARD_TOKEN')
TG_TOKEN = _env(f'{ROOT}/.env', 'TELEGRAM_BOT_TOKEN')
TG_CHAT = _env(f'{ROOT}/.env', 'TELEGRAM_CHAT_ID')

try:
    ST = json.load(open(STATE))
except Exception:
    ST = {}
ST.setdefault('fired', {})
ST.setdefault('sent', [])
ST.setdefault('pending', [])
OUT = []


def save():
    tmp = STATE + '.tmp'
    json.dump(ST, open(tmp, 'w'))
    os.replace(tmp, STATE)


def emit(key, kind, msg):
    """Record an alert once per key; it is sent at the end of the tick."""
    if key in ST['fired']:
        return
    now = datetime.now(timezone.utc)
    ST['fired'][key] = now.isoformat(timespec='seconds')
    cutoff = (now - timedelta(days=3)).isoformat()
    ST['fired'] = {k: v for k, v in ST['fired'].items() if v >= cutoff}
    line = f'{now:%a %H:%M}Z {kind} | {msg}'
    print(line, flush=True)
    with open(LOG, 'a') as f:
        f.write(line.replace('\n', ' / ') + '\n')
    OUT.append(f'{kind}: {msg}')


def tg_send(text):
    if DRY or not (TG_TOKEN and TG_CHAT):
        print('[not sent' + (' (dry)' if DRY else ': telegram settings missing') + '] ' + text.replace('\n', ' / '), flush=True)
        return DRY
    data = urllib.parse.urlencode({'chat_id': TG_CHAT, 'text': text[:4000], 'disable_web_page_preview': 'true'}).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage', data=data)
    try:
        return bool(json.load(urllib.request.urlopen(req, context=CTX, timeout=15)).get('ok'))
    except urllib.error.HTTPError as e:   # never log the URL: it carries the token
        print(f'telegram send failed: HTTP {e.code}', file=sys.stderr, flush=True)
    except Exception as e:
        print(f'telegram send failed: {type(e).__name__}', file=sys.stderr, flush=True)
    return False


def flush():
    """Send this tick's alerts as one message (plus any unsent backlog), rate-limited."""
    now = time.time()
    ST['sent'] = [t for t in ST['sent'] if now - t < 3600]
    msgs = ST['pending'][-5:]
    if OUT:
        msgs.append(f"WATCH {datetime.now(timezone.utc):%H:%M}Z\n" + '\n'.join(OUT))
    ST['pending'] = []
    for m in msgs:
        if len(ST['sent']) >= MAX_MSGS_PER_HOUR:
            ST['suppressed'] = ST.get('suppressed', 0) + 1
            continue
        if ST.get('suppressed'):
            m = f"({ST.pop('suppressed')} messages held back by the rate limit; see logs/risk_watch_alerts.log)\n" + m
        if tg_send(m):
            ST['sent'].append(now)
        else:
            ST['pending'].append(m)
    OUT.clear()
    save()


def jget(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {})
    return json.load(urllib.request.urlopen(req, context=CTX if url.startswith('https') else None, timeout=timeout))


def pct(a, b):
    return (a / b - 1) * 100


def btc():
    k = jget('https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=1m&limit=241')
    c = [float(x[4]) for x in k]
    return {'px': c[-1], 'm15': pct(c[-1], c[-16]), 'm60': pct(c[-1], c[-61]), 'h4': pct(c[-1], c[0])}


def provisional_regime():
    """State/ADX if the forming 4h bar closed now: 248 closed bars + the forming bar, live classifier."""
    k = jget('https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=4h&limit=249')
    C = [Candle(symbol='BTCUSDT', timeframe='4h', open_time=datetime.fromtimestamp(x[0] / 1000, timezone.utc),
                close_time=datetime.fromtimestamp(x[6] / 1000, timezone.utc), open=float(x[1]), high=float(x[2]),
                low=float(x[3]), close=float(x[4]), volume=float(x[5])) for x in k]
    s = compute_regime_snapshot(C)
    return s.state, s.adx


def next_state(prev, snap_state, adx):
    """ADXBAND deadband, as main._refresh_btc_regime / research/build_regime_survival.replay."""
    if prev in ('bull', 'bear'):
        return 'neutral' if (adx is not None and adx < EXIT_ADX) else (snap_state if snap_state in ('bull', 'bear') else prev)
    return snap_state if (snap_state in ('bull', 'bear') and (adx or 0) >= ENTER_ADX) else 'neutral'


def arm_day(trades, meta, day):
    out = {}
    for t in sorted(trades, key=lambda t: t['timestamp']):
        if not t['timestamp'].startswith(day):
            continue
        arm = (meta.get(t['timestamp']) or {}).get('arm')
        if arm is None:
            try:
                arm = json.loads(t.get('signal_data') or '{}').get('session')
            except Exception:
                arm = None
        a = out.setdefault(arm or '?', {'r': 0.0, 'n': 0, 'streak': 0, 'usd': 0.0})
        r = t.get('pnl_r') or 0.0
        a['r'] += r
        a['n'] += 1
        a['usd'] += t.get('pnl_usd') or 0.0
        a['streak'] = a['streak'] + 1 if r < 0 else 0
    return out


def book(pos):
    out = {}
    for p in pos if isinstance(pos, list) else []:
        u = p.get('u_r')
        if u is None:
            continue
        a = out.setdefault(p.get('arm') or '?', {'n': 0, 'u': 0.0, 'red': 0})
        a['n'] += 1
        a['u'] += u
        a['red'] += u < 0
    return out


def hours_txt(hs):
    return ','.join(str(h) for h in hs) if hs else 'off'


def tick(mode='loop'):
    now = datetime.now(timezone.utc)
    day, H, wd = now.strftime('%Y-%m-%d'), now.strftime('%Y-%m-%dT%H'), DAYS[now.weekday()]
    d = jget(API, {'Authorization': f'Bearer {DASH_TOKEN}'}, timeout=30)
    v, m = d.get('v3') or {}, (d.get('bot') or {}).get('metrics')
    if not m:
        emit(f'OPS:bot:{H}', 'OPS', 'bot /metrics unreachable through the dashboard; bot state unknown')
        return
    arms = (m.get('arms') or {}).get('arms') or {}
    rh = v.get('regime_health') or {}
    state, adx, d3 = rh.get('state') or m.get('btc_regime'), rh.get('adx'), rh.get('d3')
    fade = state == 'bull' and d3 is not None and d3 <= -4
    try:
        b = btc()
    except Exception:
        b = None
    P = v.get('performance') or {}
    ad = arm_day(d.get('trades') or [], P.get('trade_meta') or {}, day)
    bk = book(v.get('positions'))
    armed_any = any(a.get('armed_now') for a in arms.values())
    # an arm whose allowed regimes exclude the current state is off, whatever its base hours say
    age = rh.get('age_bars')
    today_hours = {k: (a.get('hours_by_weekday', {}).get(wd, [])
                       if (not a.get('regimes') or state in a['regimes'])
                       and (k not in AGE_CAPS or (age is not None and age <= AGE_CAPS[k])) else [])
                   for k, a in arms.items()}
    # drawdown vs the equity pause, in $ and in R at the active risk per leg
    rk, rc = d.get('risk') or {}, v.get('risk_context') or {}
    eq, peak = rk.get('current_equity') or m.get('equity'), rk.get('peak_equity')
    dd = rk.get('current_drawdown_pct') if rk.get('current_drawdown_pct') is not None else m.get('drawdown_pct')
    pause_at, risk_pct = rc.get('pause_at', 0.35), rk.get('risk_pct_active') or rc.get('risk_pct_default') or 10.0
    to_pause = (eq - peak * (1 - pause_at)) if (eq and peak) else None
    to_pause_r = to_pause / (eq * risk_pct / 100) if (to_pause is not None and eq) else None
    dd_txt = (f"Drawdown {dd * 100:.1f}% (equity ${eq:.2f}); {pause_at * 100:.0f}% pause is ${to_pause:.0f} "
              f"({to_pause_r:.1f}R) away; {risk_pct:g}%/leg") if (dd is not None and to_pause is not None) else 'Drawdown n/a'

    def summary(next_close=False):
        band = f'neutral below {EXIT_ADX}' if state in ('bull', 'bear') else f'bull/bear from {ENTER_ADX}'
        lines = [f"Regime {state}" + (f", ADX {adx:.1f} ({d3:+.1f} over 3 bars; {band})"
                                      if adx is not None and d3 is not None else '')]
        if next_close:
            try:
                ps, pa = provisional_regime()
                ch = (now.hour // 4 + 1) * 4 % 24
                nxt = next_state(state, ps, pa)
                lines.append(f"At the {ch:02d}:00 close, if BTC holds: {nxt}" + (f" (ADX {pa:.1f})" if pa is not None else ''))
            except Exception:
                pass
        if fade:
            lines.append('LON-BULL-FADE ON. ' + FADE_NOTE)
        lines.append(dd_txt + (' (reduced-risk mode)' if rc.get('reduced_mode') else ''))
        if m.get('paused'):
            lines.append('Bot is PAUSED: no new entries until /resume')
        if b:
            lines.append(f"BTC {b['px']:.0f}: 1h {b['m60']:+.2f}%, 4h {b['h4']:+.2f}%")
        legs = '; '.join(f"{k} {x['r']:+.2f}R/{x['n']} legs" for k, x in ad.items()) or 'no closed legs'
        lines.append(f"Today: {legs}. Open: {sum(x['n'] for x in bk.values())} legs {sum(x['u'] for x in bk.values()):+.2f}R")
        lines.append('Armed today: ' + '; '.join(f"{k} {hours_txt(h)}" for k, h in today_hours.items()))
        return '\n'.join(lines)

    if mode == 'start':
        quiet = time.time() - ST.get('online_t', 0) < 6 * 3600       # restarts within 6h stay quiet
        ST['online_t'] = time.time()
        if 'dd_lvl' not in ST and dd is not None:
            ST['dd_lvl'] = max([lv for lv in DD_LEVELS if dd >= lv], default=0)
        if state:
            ST.setdefault('regime', state)
        # conditions already true at start go into the ONLINE summary, not out as fresh alerts
        stamp = now.isoformat(timespec='seconds')
        for arm, x in ad.items():
            for thr in (-1.0, -2.0, -3.0):
                if x['r'] <= thr:
                    ST['fired'].setdefault(f'DAYR:{arm}:{day}:{thr}', stamp)
            if x['streak'] >= 3:
                ST['fired'].setdefault(f'STREAK:{arm}:{day}', stamp)
        if rc.get('reduced_mode'):
            ST['fired'].setdefault(f'OPS:reduced:{day}', stamp)
        if m.get('paused'):
            ST['fired'].setdefault(f'OPS:paused:{day}', stamp)
        if not quiet:
            emit(f'START:{now:%Y-%m-%dT%H:%M}', 'ONLINE', 'risk watch started\n' + summary(next_close=True))
        return
    # regime state change (persisted across restarts)
    if ST.get('regime') and state and state != ST['regime']:
        emit(f'REGIME:{H}:{state}', 'REGIME', f"{ST['regime']} -> {state} (ADX {adx}). Armed today now: "
             + '; '.join(f"{k} {hours_txt(h)}" for k, h in today_hours.items()))
    if state:
        ST['regime'] = state
    # briefs before London and NY
    if now.weekday() < 5 and now.hour in (8, 13) and 40 <= now.minute < 50:
        emit(f'BRIEF:{day}:{now.hour}', 'BRIEF', ('Pre-London\n' if now.hour == 8 else 'Pre-NY\n') + summary(next_close=True))
    # end-of-day summary
    if now.weekday() < 5 and now.hour == 21 and 5 <= now.minute < 15:
        n_alerts = sum(1 for v_ in ST['fired'].values() if v_.startswith(day))
        legs = '; '.join(f"{k} {x['r']:+.2f}R/{x['n']} legs (${x['usd']:+.2f})" for k, x in ad.items()) or 'no legs'
        emit(f'EOD:{day}', 'EOD', f"Day close: {legs}\n{dd_txt}\nAlerts today: {n_alerts}")
    # pre-hour go/no-go, only when a flag is up
    for arm, hs in today_hours.items():
        for h in hs:
            if now.hour == (h - 1) % 24 and now.minute >= 53:
                flags = []
                if fade and arm == 'london':
                    flags.append(f'LON-BULL-FADE ON (ADX {d3:+.1f} over 3 bars)')
                if state in ('bull', 'bear') and adx is not None and adx - EXIT_ADX < 1.0:
                    flags.append(f'ADX {adx:.1f}, {adx - EXIT_ADX:.1f} above the neutral exit')
                if b and b['m60'] <= -0.5 and state != 'bear':
                    flags.append(f"BTC 1h {b['m60']:+.2f}%")
                if ad.get(arm, {}).get('r', 0) <= -0.5:
                    flags.append(f"{arm} day {ad[arm]['r']:+.2f}R")
                if bk.get(arm, {}).get('red', 0) >= 2 and bk[arm]['u'] <= -0.4:
                    flags.append(f"{bk[arm]['red']}/{bk[arm]['n']} open {arm} legs red ({bk[arm]['u']:+.2f}R)")
                if to_pause_r is not None and to_pause_r <= 1.0:
                    flags.append(f'equity pause {to_pause_r:.1f}R away')
                if flags:
                    note = ('\n' + FADE_NOTE) if (fade and arm == 'london') else ''
                    emit(f'PREHOUR:{arm}:{day}:{h}', 'PRE-HOUR', f"{arm} {h:02d}:00 arms in {60 - now.minute} min. Flags: "
                         + '; '.join(flags) + note)
    # tape against the arms' side
    if b and (armed_any or bk):
        adverse = (b['m60'] >= 0.8 or b['m15'] >= 0.5) if state == 'bear' else (b['m60'] <= -0.8 or b['m15'] <= -0.5)
        if adverse:
            emit(f'TAPE:{H}', 'TAPE', f"BTC 15m {b['m15']:+.2f}%, 1h {b['m60']:+.2f}% at {b['px']:.0f}. Armed: "
                 + (', '.join(k for k, a in arms.items() if a.get('armed_now')) or 'none')
                 + f". Open legs {sum(x['n'] for x in bk.values())} ({sum(x['u'] for x in bk.values()):+.2f}R)")
    # open book
    for arm, x in bk.items():
        if (x['n'] >= 3 and x['red'] == x['n'] and x['u'] <= -0.5) or x['u'] <= -0.8:
            emit(f'BOOK:{arm}:{H}', 'BOOK', f"{arm}: {x['red']}/{x['n']} open legs red, unrealized {x['u']:+.2f}R")
    # day R per arm
    for arm, x in ad.items():
        for thr in (-1.0, -2.0, -3.0):
            if x['r'] <= thr:
                emit(f'DAYR:{arm}:{day}:{thr}', 'DAY', f"{arm} day {x['r']:+.2f}R over {x['n']} legs (crossed {thr:+.0f}R). {dd_txt}")
        if x['streak'] >= 3:
            emit(f'STREAK:{arm}:{day}', 'DAY', f"{arm}: {x['streak']} straight losers, day {x['r']:+.2f}R")
    # drawdown levels (re-arm a level once drawdown is 3 points back below it) and pause proximity
    if dd is not None:
        lvl, last = max([lv for lv in DD_LEVELS if dd >= lv], default=0), ST.get('dd_lvl', 0)
        if lvl > last:
            emit(f'DD:{lvl}:{now:%Y-%m-%dT%H:%M}', 'DRAWDOWN', f"crossed {lvl * 100:.0f}%. {dd_txt}")
            ST['dd_lvl'] = lvl
        elif lvl < last and dd < last - 0.03:
            ST['dd_lvl'] = lvl
        if to_pause_r is not None and to_pause_r <= 1.0:
            emit(f'PAUSEPROX:{day}', 'DRAWDOWN', f"equity pause within 1R. {dd_txt}")
    # stop-loss cluster
    stops = sorted(t['timestamp'] for t in (d.get('trades') or [])
                   if t.get('exit_reason') == 'stop_loss' and t['timestamp'].startswith(day))
    for a, c in zip(stops, stops[1:]):
        gap = (datetime.fromisoformat(c) - datetime.fromisoformat(a)).total_seconds()
        if gap <= 600:
            emit(f'STOPS:{a}', 'STOPS', f"2 stop-loss exits within {gap / 60:.0f} min ({a[11:16]}Z, {c[11:16]}Z)")
    # provisional regime at the next 4h close
    close_h = (now.hour // 4 + 1) * 4
    mins_left = (close_h - now.hour) * 60 - now.minute
    if 0 < mins_left <= 60 and time.time() - ST.get('prov_t', 0) >= 300:
        ST['prov_t'] = time.time()
        ps, pa = provisional_regime()
        nxt = next_state(state, ps, pa)
        if nxt != state:
            lead = f"if BTC holds ({b['px']:.0f})" if b else 'if BTC holds'
            emit(f'PROV:{day}:{close_h % 24}:{nxt}', 'REGIME', lead + f", ADX {pa:.1f} at the {close_h % 24:02d}:00 close: "
                 f"{state} becomes {nxt} ({mins_left} min left)")
    # ops
    O = v.get('ops') or {}
    for u, s in (O.get('units') or {}).items():
        if s != 'active':
            emit(f'OPS:unit:{u}:{H}', 'OPS', f'{u} is {s}')
    for u, s in (O.get('pm2') or {}).items():
        if s != 'online':
            emit(f'OPS:pm2:{u}:{H}', 'OPS', f'pm2 {u} is {s}')
    for k, s in (O.get('freshness_s') or {}).items():
        if k in FRESH_LIMIT and s is not None and s > FRESH_LIMIT[k]:
            emit(f'OPS:fresh:{k}:{H}', 'OPS', f'{k} stale for {s / 60:.0f} min')
    if m.get('paused'):
        emit(f'OPS:paused:{day}', 'OPS', 'bot reports paused (no new entries)')
    if rc.get('reduced_mode'):
        emit(f'OPS:reduced:{day}', 'OPS', f'bot in reduced-risk mode ({risk_pct:g}%/leg)')
    for name, t in (m.get('task_health') or {}).items():
        if isinstance(t, dict) and t.get('critical') and not t.get('healthy'):
            emit(f'OPS:task:{name}:{H}', 'OPS', f'critical task {name} unhealthy')


def main():
    if '--test' in sys.argv:
        ok = tg_send('WATCH test: Bitana risk watch can reach this chat. Alerts will come from here.')
        print('telegram test sent' if ok else 'telegram test FAILED')
        sys.exit(0 if ok else 1)
    try:
        tick('start')
    except Exception:
        traceback.print_exc(file=sys.stderr)
    flush()
    if '--once' in sys.argv:
        return
    fails = 0
    while True:
        time.sleep(60 - time.time() % 60 + 2)
        try:
            tick()
            fails = 0
        except Exception as e:
            fails += 1
            traceback.print_exc(file=sys.stderr)
            if fails == 3:
                emit(f"OPS:watch:{datetime.now(timezone.utc):%Y-%m-%dT%H}", 'OPS',
                     f'watch failing 3x in a row: {type(e).__name__}. Dashboard or network may be down.')
        flush()


if __name__ == '__main__':
    main()
