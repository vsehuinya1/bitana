"""Verdict (registered bar, reports/wd_sizing_prereg.md) + report-only lines from wdsize.run()."""
import sys
import numpy as np
import pandas as pd
sys.argv = ['x']
sys.path.insert(0, '/root/bitana/research/wdsize')
import wdsize as W

res = pd.read_pickle(W.OUT + 'runs.pkl')['res']
R = pd.DataFrame(res).set_index(['period', 'design'])
print('=== registered bar per period vs A0 (1 total > A0, 2 ratio >= A0, 3 no liquidation and min equity >= $40) ===')
verdict = {}
for d in W.DESIGNS:
    if d == 'A0':
        continue
    cells, ok = [], True
    for per in ('2020-21', '2022-23', '2024-26'):
        a, x = R.loc[(per, 'A0')], R.loc[(per, d)]
        c1, c2, c3 = x.total > a.total, x.ratio >= a.ratio, (not x.liq) and x.min_eq >= 40
        ok &= c1 and c2 and c3
        cells.append(f"{per}: ${x.total:+6.1f} vs {a.total:+6.1f} | ratio {x.ratio:4.2f} vs {a.ratio:4.2f} | min eq ${x.min_eq:5.1f} "
                     f"-> {'1' if c1 else '-'}{'2' if c2 else '-'}{'3' if c3 else '-'}")
    verdict[d] = ok
    print(f"{d}: {'PASS' if ok else 'FAIL'}\n   " + '\n   '.join(cells))
print('\n=== $ by year (period runs from $104; 2026 = to 09-24) ===')
for d in W.DESIGNS:
    ys = {}
    for per in ('2020-21', '2022-23', '2024-26'):
        ys.update(R.loc[(per, d)].years)
    print(f"{d}: " + ' '.join(f"{y}:{v:+6.1f}" for y, v in sorted(ys.items())))
print('\n=== six crash days, continuous run: worst marked moment vs the day-start equity, and the day-end change ($) ===')
DAYS = ['2021-09-07', '2023-08-17', '2024-01-03', '2024-04-12', '2025-10-10', '2026-01-18']
for d in ('A0', 'A2', 'A3', 'A5', 'B1', 'B2'):
    r, _ = W.run(d, '2020-10-01', '2026-09-24', want_curve=True)
    El, Ec, j0 = r['E_low'], r['E_cls'], r['j0']
    cells = []
    for day in DAYS:
        a = W.bi(day) - j0; b = a + 288
        start = Ec[a - 1]
        cells.append(f"{day}: {El[a:b].min() - start:+6.1f} / {Ec[b - 1] - start:+6.1f}")
    print(f"{d}: " + ' | '.join(cells))
print('\nPASSING:', [d for d, v in verdict.items() if v])
