"""Read-only check of the test sub-account for one symbol: position, open stop (algo) orders, recent orders, fills,
funding charged. Usage: venv/bin/python -m fsettle_live.verify SYMBOL [settlement 'YYYY-mm-dd HH:MM']"""
import asyncio
import calendar
import sys
import time

from fsettle_live import engine as E


async def main(sym: str, s_utc: str | None):
    k = E.read_env('/root/bitana/.env.fsettle', 'FS_API_KEY')
    sec = E.read_env('/root/bitana/.env.fsettle', 'FS_API_SECRET')
    r = E.Rest(k, sec)
    await r.start()
    await r.sync_clock(3)
    s = calendar.timegm(time.strptime(s_utc, '%Y-%m-%d %H:%M')) * 1000 if s_utc else E.now_ms() - 3_600_000
    a = await r.req('GET', '/fapi/v2/account', signed=True)
    print(f"wallet {a.get('totalWalletBalance')}  available {a.get('availableBalance')}")
    pr = await r.req('GET', '/fapi/v2/positionRisk', {'symbol': sym}, signed=True)
    for p in pr if isinstance(pr, list) else []:
        print(f"position {p['symbol']} amt {p['positionAmt']} entry {p['entryPrice']} mark {p['markPrice']} "
              f"uPnL {p['unRealizedProfit']} lev {p.get('leverage')} margin {p.get('marginType')}")
    ao = await r.req('GET', '/fapi/v1/openAlgoOrders', {'symbol': sym}, signed=True)
    print('open algo orders:', ao if not isinstance(ao, (list, dict)) else
          [(o.get('clientAlgoId'), o.get('side'), o.get('orderType') or o.get('type'), o.get('quantity'), o.get('triggerPrice'),
            o.get('reduceOnly'), o.get('algoStatus')) for o in (ao if isinstance(ao, list) else ao.get('orders', []))])
    od = await r.req('GET', '/fapi/v1/allOrders', {'symbol': sym, 'startTime': s - 60_000}, signed=True)
    for o in od if isinstance(od, list) else []:
        print(f"order {o['clientOrderId']} {o['side']} {o['type']} {o.get('timeInForce')} {o['status']} qty {o['executedQty']}/"
              f"{o['origQty']} avg {o['avgPrice']} reduceOnly {o['reduceOnly']} t {o['updateTime'] - s} ms after s")
    tr = await r.req('GET', '/fapi/v1/userTrades', {'symbol': sym, 'startTime': s - 60_000}, signed=True)
    for t in tr if isinstance(tr, list) else []:
        print(f"fill {t['side']} {t['qty']} @ {t['price']} maker {t['maker']} pnl {t['realizedPnl']} fee {t['commission']} "
              f"{t['commissionAsset']} t {t['time'] - s} ms after s")
    inc = await r.req('GET', '/fapi/v1/income', {'symbol': sym, 'incomeType': 'FUNDING_FEE', 'startTime': s - 120_000}, signed=True)
    print('funding fees since s-2min:', [(i['income'], i['time'] - s) for i in inc] if isinstance(inc, list) else inc)
    await r.close()


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
