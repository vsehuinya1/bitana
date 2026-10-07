"""Offline tests for fsettle_live/engine.py with a fake Binance (no network, no orders).
Run: /root/bitana/venv/bin/python -m pytest -q fsettle_live/test_engine.py
"""
import asyncio
import json
import os
import tempfile
import time

import pytest
import yaml

from fsettle_live import engine as E


def make_engine(tmp, **over):
    cfg = yaml.safe_load(open('/root/bitana/fsettle_live/config.yaml'))
    cfg.update({'data_dir': tmp, 'telegram': False, 'keys_env': os.path.join(tmp, 'nokeys'),
                'wd_keys_env': os.path.join(tmp, 'nowd')})
    cfg.update(over)
    p = os.path.join(tmp, 'cfg.yaml')
    yaml.safe_dump(cfg, open(p, 'w'))
    eng = E.Engine(p)
    eng.spec = {'XUSDT': E.Spec('0.0001', '1', 5.0), 'YUSDT': E.Spec('0.01', '0.1', 5.0),
                'ZUSDT': E.Spec('0.001', '1', 5.0), 'WUSDT': E.Spec('0.001', '1', 5.0)}
    return eng


class FakeRest:
    """Records every call; answers like Binance for the paths the engine uses."""

    def __init__(self, fill_qty='60', avg='0.1000', stop_fail=0, pos_after=-60.0, entry_err=None, unc=5, cum_quote=None,
                 order_avg='0.1000', fill_px='0.1000'):
        self.calls, self.offset, self.unc = [], 0, unc
        self.fill_qty, self.avg, self.stop_fail, self.pos, self.entry_err = fill_qty, avg, stop_fail, pos_after, entry_err
        self.cum_quote, self.order_avg, self.fill_px = cum_quote, order_avg, fill_px
        self.used_weight = 0

    async def sync_clock(self, n=5):
        return self.offset, self.unc

    async def close(self):
        pass

    async def req(self, method, path, params=None, signed=False, base=None):
        params = dict(params or {})
        self.calls.append((method, path, params))
        if path in ('/fapi/v1/marginType', '/fapi/v1/leverage'):
            return {'code': 200, 'msg': 'success'} if path.endswith('Type') else {'leverage': 5}
        if path == '/fapi/v1/order' and method == 'POST':
            if params.get('timeInForce') == 'IOC':
                if self.entry_err:
                    return {'code': self.entry_err, 'msg': 'rejected'}
                out = {'status': 'FILLED', 'executedQty': self.fill_qty, 'avgPrice': self.avg, 'updateTime': E.now_ms(),
                       'orderId': 7}
                if self.cum_quote is not None:
                    out['cumQuote'] = self.cum_quote
                return out
            self.pos = 0.0
            return {'status': 'FILLED', 'executedQty': params['quantity'], 'avgPrice': '0.0990'}
        if path == '/fapi/v1/order' and method == 'GET':
            return {'status': 'FILLED', 'executedQty': self.fill_qty, 'avgPrice': self.order_avg}
        if path == '/fapi/v1/algoOrder' and method == 'POST':
            if self.stop_fail > 0:
                self.stop_fail -= 1
                return {'code': -1008, 'msg': 'overloaded'}
            return {'algoId': 1, 'clientAlgoId': params['clientAlgoId']}
        if path == '/fapi/v1/algoOrder' and method == 'DELETE':
            return {'code': '200', 'msg': 'success'}
        if path == '/fapi/v2/positionRisk':
            return [{'symbol': params.get('symbol', 'XUSDT'), 'positionAmt': str(self.pos)}]
        if path == '/fapi/v1/userTrades':
            return [{'side': 'SELL', 'qty': '60', 'price': self.fill_px, 'realizedPnl': '0', 'commission': '0.003',
                     'commissionAsset': 'USDT'},
                    {'side': 'BUY', 'qty': '60', 'price': '0.0990', 'realizedPnl': '0.06', 'commission': '0.003',
                     'commissionAsset': 'USDT'}]
        if path == '/fapi/v1/income':
            return []
        return {}


def ev(sym='XUSDT', dt_ms=400, f=-0.002):
    e = E.Ev(sym, E.now_ms() + dt_ms, f, -1 if f < 0 else 1)
    e.last_p = 0.1
    e.paper = E.Sim(250, 200, None)
    e.dry = E.Sim(250, 200, 50)
    return e


def run(coro):
    return asyncio.run(coro)


def test_rounding_and_cap():
    assert E.q_round(0.0995, '0.0001', up=False) == E.Decimal('0.0995')
    assert E.q_round(6 / 0.1, '1', up=True) == E.Decimal('60')
    assert E.q_round(6 / 0.1234, '0.1', up=True) == E.Decimal('48.7')
    assert E.fmt(E.Decimal('60')) == '60' and E.fmt(E.Decimal('0.0990')) == '0.099'


def test_live_happy_path_short():
    with tempfile.TemporaryDirectory() as tmp:
        eng = make_engine(tmp, mode='live', hold_min=0.02)        # 1.2 s hold for the test
        eng.keys_ok, eng.rest = True, FakeRest()
        e = ev()
        row = {}
        t0 = E.now_ms()
        run(eng.live_trade(e, row))
        paths = [(m, p) for m, p, _ in eng.rest.calls]
        entry = [c for c in eng.rest.calls if c[1] == '/fapi/v1/order' and c[2].get('timeInForce') == 'IOC'][0][2]
        assert entry['side'] == 'SELL' and entry['type'] == 'LIMIT' and entry['quantity'] == '60'
        assert entry['price'] == '0.0995'                            # 0.5% below 0.1 for a short
        stop = [c for c in eng.rest.calls if c[1] == '/fapi/v1/algoOrder' and c[0] == 'POST'][0][2]
        assert stop['side'] == 'BUY' and stop['type'] == 'STOP_MARKET' and stop['reduceOnly'] == 'true'
        assert stop['triggerPrice'] == '0.102' and stop['workingType'] == 'CONTRACT_PRICE'
        assert ('POST', '/fapi/v1/marginType') in paths and ('POST', '/fapi/v1/leverage') in paths
        # entry not sent before s + delay
        assert row['send_ex_ms'] >= 250 - 5, row
        # time exit = market reduce-only BUY, stop cancelled afterwards, accounting read
        ex = [c for c in eng.rest.calls if c[1] == '/fapi/v1/order' and c[2].get('type') == 'MARKET'][0][2]
        assert ex['side'] == 'BUY' and ex['reduceOnly'] == 'true'
        assert ('DELETE', '/fapi/v1/algoOrder') in paths and ('GET', '/fapi/v1/userTrades') in paths
        assert row['exit_reason'] == 'time' and abs(row['pnl_usd'] - 0.054) < 1e-9
        assert row['net_bps'] == pytest.approx(100 - 10, abs=0.01)
        assert eng.open_live == {} and eng.n_live == 1


@pytest.mark.parametrize('cum_quote,order_avg,trigger,queried', [
    ('6.0', '0', '0.102', False),          # avgPrice 0 in the IOC response, cumQuote 6.0 / 60 = 0.1
    ('0', '0.1000', '0.102', True),        # no cumQuote either: the order is queried by client id
    ('0', '0', '0.102', True),             # nothing known: stop from the reference price 0.1
])
def test_entry_avgprice_zero_still_places_stop(cum_quote, order_avg, trigger, queried):
    with tempfile.TemporaryDirectory() as tmp:
        eng = make_engine(tmp, mode='live', hold_min=0.02)
        eng.keys_ok, eng.rest = True, FakeRest(avg='0.00000', cum_quote=cum_quote, order_avg=order_avg)
        row = {}
        run(eng.live_trade(ev(), row))
        stop = [c for c in eng.rest.calls if c[1] == '/fapi/v1/algoOrder' and c[0] == 'POST']
        assert len(stop) == 1 and stop[0][2]['triggerPrice'] == trigger
        assert (('GET', '/fapi/v1/order') in [(m, p) for m, p, _ in eng.rest.calls]) == queried
        assert row['exit_reason'] == 'time' and row['entry'] == pytest.approx(0.1)     # entry from the fills
        assert row['net_bps'] == pytest.approx(100 - 10, abs=0.01)


def test_stop_corrected_from_fills_when_fill_price_unknown():
    with tempfile.TemporaryDirectory() as tmp:
        eng = make_engine(tmp, mode='live', hold_min=0.02)
        eng.keys_ok, eng.rest = True, FakeRest(avg='0', cum_quote='0', order_avg='0', fill_px='0.0990')
        row = {}
        run(eng.live_trade(ev(), row))
        stops = [c[2] for c in eng.rest.calls if c[1] == '/fapi/v1/algoOrder' and c[0] == 'POST']
        dels = [c[2]['clientAlgoId'] for c in eng.rest.calls if c[1] == '/fapi/v1/algoOrder' and c[0] == 'DELETE']
        assert [x['triggerPrice'] for x in stops] == ['0.102', '0.101']          # ref 0.1 first, then real 0.099 * 1.02
        assert dels[0] == stops[0]['clientAlgoId'] and dels[-1] == stops[1]['clientAlgoId']   # old cancelled after new placed
        i_new = [k for k, c in enumerate(eng.rest.calls) if c[1] == '/fapi/v1/algoOrder' and c[0] == 'POST'][1]
        i_del = [k for k, c in enumerate(eng.rest.calls) if c[1] == '/fapi/v1/algoOrder' and c[0] == 'DELETE'][0]
        assert i_new < i_del
        assert row['stop_trigger'] == '0.101' and row['entry'] == pytest.approx(0.099)


def test_stop_placement_failure_closes_at_once():
    with tempfile.TemporaryDirectory() as tmp:
        eng = make_engine(tmp, mode='live', hold_min=5)
        eng.keys_ok, eng.rest = True, FakeRest(stop_fail=2)
        row = {}
        t0 = time.time()
        run(eng.live_trade(ev(), row))
        assert time.time() - t0 < 10                                 # did not wait for the 5-min hold
        assert row['exit_reason'] == 'stop_failed' and row['stop'].startswith('FAILED')
        mk = [c for c in eng.rest.calls if c[1] == '/fapi/v1/order' and c[2].get('type') == 'MARKET']
        assert mk and mk[0][2]['reduceOnly'] == 'true'
        assert eng.open_live == {}


def test_stop_already_hit_no_market_close():
    with tempfile.TemporaryDirectory() as tmp:
        eng = make_engine(tmp, mode='live', hold_min=0.02)
        eng.keys_ok, eng.rest = True, FakeRest(pos_after=0.0)       # position gone = stop filled
        row = {}
        run(eng.live_trade(ev(), row))
        mk = [c for c in eng.rest.calls if c[1] == '/fapi/v1/order' and c[2].get('type') == 'MARKET']
        assert not mk and row['exit_reason'] == 'stop'


def test_clock_uncertainty_skips():
    with tempfile.TemporaryDirectory() as tmp:
        eng = make_engine(tmp, mode='live')
        eng.keys_ok, eng.rest = True, FakeRest(unc=200)
        row = {}
        run(eng.live_trade(ev(dt_ms=9000), row))
        assert row['skip'].startswith('clock uncertainty')
        assert not [c for c in eng.rest.calls if c[1] == '/fapi/v1/order']


def test_notional_bounds_skip():
    with tempfile.TemporaryDirectory() as tmp:
        eng = make_engine(tmp, mode='live')
        eng.spec['XUSDT'] = E.Spec('0.0001', '100', 5.0)             # step forces 100 x 0.1 = $10 > $9
        eng.keys_ok, eng.rest = True, FakeRest()
        row = {}
        run(eng.live_trade(ev(), row))
        assert row['skip'].startswith('notional')


def test_entry_rejection_counts_errors_and_halts():
    with tempfile.TemporaryDirectory() as tmp:
        eng = make_engine(tmp, mode='live')
        eng.keys_ok, eng.rest = True, FakeRest(entry_err=-2019)
        for _ in range(3):
            run(eng.live_trade(ev(), {}))
        assert eng.halted and eng.entry_block_reason().startswith('halted')


def test_selection_ranking_max_open_and_late_arrival():
    with tempfile.TemporaryDirectory() as tmp:
        eng = make_engine(tmp, mode='dry', max_open=2)
        started = []

        async def fake_run(e):
            started.append(e.sym)

        async def fake_finish(e):
            pass
        eng.run_event, eng.finish_unselected = fake_run, fake_finish

        async def go():
            T = E.now_ms() + 50_000
            for sym, f in (('XUSDT', -0.002), ('YUSDT', -0.010), ('ZUSDT', 0.0015)):
                eng.events[(sym, T)] = E.Ev(sym, T, f, -1 if f < 0 else 1)
            eng.select(T)
            await asyncio.sleep(0)
            eng.events[('WUSDT', T)] = E.Ev('WUSDT', T, -0.05, -1)      # arrives late, biggest |f|
            eng.select(T)
            await asyncio.sleep(0)
            return T
        T = run(go())
        assert started == ['YUSDT', 'XUSDT']                           # top-2 by |f_prev|, no re-runs
        assert eng.events[('ZUSDT', T)].skip == 'max_open reached'
        assert eng.events[('WUSDT', T)].skip == 'max_open reached'


def test_live_blocked_without_verified_keys_and_daily_cap():
    with tempfile.TemporaryDirectory() as tmp:
        eng = make_engine(tmp, mode='live')
        assert eng.entry_block_reason() == 'live mode but keys not verified'
        eng.keys_ok = True
        assert eng.entry_block_reason() == ''
        eng.day_pnl[time.strftime('%Y-%m-%d', time.gmtime())] = -1.6
        assert eng.entry_block_reason().startswith('daily loss cap')


def test_sim_short_stop_and_cap():
    s, H = 1_000_000, 1_800_000
    sim = E.Sim(250, 200, 50)
    sim.on_trade(s, -1, H, s - 10, 1.000, True)                       # ref
    sim.on_trade(s, -1, H, s + 100, 0.990, True)                      # before delay: ignored
    sim.on_trade(s, -1, H, s + 300, 0.998, False)                     # buy-aggressor: not an entry for a short
    sim.on_trade(s, -1, H, s + 400, 0.997, True)                      # entry (sell at bid)
    sim.on_trade(s, -1, H, s + 5000, 1.018, False)                    # +2.1% adverse → stop, exit on this buy trade
    assert sim.en == 0.997 and sim.stopped and sim.ex == 1.018 and sim.state == 'done'
    cap = E.Sim(250, 200, 50)
    cap.on_trade(s, -1, H, s - 10, 1.000, True)
    cap.on_trade(s, -1, H, s + 300, 0.990, True)                      # -1% beyond the 0.5% cap → IOC no fill
    assert cap.state == 'nofill'


def test_sim_long_cap():
    s, H = 1_000_000, 1_800_000
    cap = E.Sim(250, 200, 50)
    cap.on_trade(s, 1, H, s - 10, 1.000, False)
    cap.on_trade(s, 1, H, s + 300, 1.010, False)                       # long: +1% above ref, beyond cap → no fill
    assert cap.state == 'nofill'
    ok = E.Sim(250, 200, 50)
    ok.on_trade(s, 1, H, s - 10, 1.000, False)
    ok.on_trade(s, 1, H, s + 300, 1.003, False)                        # within cap → buy at ask
    assert ok.state == 'open' and ok.en == 1.003


def test_recover_closes_overdue_position_and_halts_on_strays():
    with tempfile.TemporaryDirectory() as tmp:
        eng = make_engine(tmp, mode='live')
        eng.key = 'k'
        eng.keys_ok, eng.rest = True, FakeRest()
        eng.open_live = {'XUSDT': {'s': E.now_ms() - 3_600_000, 'qty': '60', 'side': 'SELL', 'close_side': 'BUY',
                                   'stop_cid': 'sx', 'exit_due': E.now_ms() - 1_800_000, 'entry': 0.1}}
        run(eng.recover())
        mk = [c for c in eng.rest.calls if c[1] == '/fapi/v1/order' and c[2].get('type') == 'MARKET']
        assert mk and mk[0][2]['side'] == 'BUY' and eng.open_live == {}
        rows = [json.loads(l) for l in open(os.path.join(tmp, 'events.jsonl'))]
        assert rows[0]['exit_reason'] == 'recovered_late'
        eng2 = make_engine(tmp, mode='live')
        eng2.key, eng2.keys_ok = 'k', True
        eng2.rest = FakeRest(pos_after=-5.0)                            # a position the engine does not know
        run(eng2.recover())
        assert eng2.halted == 'unknown positions'
        assert not [c for c in eng2.rest.calls if c[1] == '/fapi/v1/order']   # strays are not touched
