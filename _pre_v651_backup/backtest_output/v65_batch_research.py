"""v65 batch research — items 1–3 on VPS.

1. ws_merged fidelity rerun (28 sym, NY)
2. v65 scale-in on coinalyze + ws_merged baselines
3. 57-symbol + London + Asia session sweeps (V5 entry, V5 exits)

Usage:
  python backtest_output/v65_batch_research.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
OUT = REPO / "backtest_output"
OUT.mkdir(parents=True, exist_ok=True)

os.environ["LEGACY_RUNNER_EXITS"] = "0"
os.environ["CAPTURE_ALL"] = "0"
os.environ["ENTRY_THESIS"] = "v645"

MIN_TEST_N = 50
PASS_TEST_AVG = 0.0
PASS_FULL_AVG = -0.05


def score(trades: list[dict]) -> dict:
    if not trades:
        return {
            "n": 0, "full_avg": 0.0, "full_R": 0.0, "wr": 0.0,
            "test_n": 0, "test_avg": 0.0, "test_R": 0.0, "test_wr": 0.0,
            "pass": False,
        }
    td = pd.DataFrame(trades).sort_values("entry_time")
    split = int(len(td) * 0.6)
    test = td.iloc[split:]
    full_avg = float(td.pnl_r.mean())
    test_avg = float(test.pnl_r.mean()) if len(test) else 0.0
    passed = len(test) >= MIN_TEST_N and test_avg > PASS_TEST_AVG and full_avg > PASS_FULL_AVG
    return {
        "n": len(td),
        "full_avg": full_avg,
        "full_R": float(td.pnl_r.sum()),
        "wr": float((td.pnl_r > 0).mean()),
        "test_n": len(test),
        "test_avg": test_avg,
        "test_R": float(test.pnl_r.sum()) if len(test) else 0.0,
        "test_wr": float((test.pnl_r > 0).mean()) if len(test) else 0.0,
        "pass": passed,
        "verdict": "KEEP" if passed else "KILL",
    }


def run_capture_variant(
    *,
    label: str,
    symbols: list[str],
    session: str,
    liq_source: str,
) -> tuple[list[dict], list, dict]:
    from backtest_output.v65_revert_config import apply_v65_revert, apply_session
    import backtest_output.v6_path_backtest as bt

    apply_v65_revert()
    apply_session(session)
    bt.LIQ_SOURCE = liq_source
    os.environ["SYMBOL_OVERRIDE"] = ",".join(symbols)
    os.environ["OUT_TAG"] = f"_{label}"

    print(f"\n{'='*60}\nRUN {label}\n  session={session} liq={liq_source} symbols={len(symbols)}\n{'='*60}", flush=True)
    trades, rpath = bt.run_capture()
    s = score(trades)
    s["label"] = label
    s["session"] = session
    s["liq_source"] = liq_source
    s["n_symbols"] = len(symbols)

    if trades:
        pd.DataFrame(trades).to_csv(OUT / f"v65_{label}_trades.csv", index=False)
    print(
        f"{s['verdict']} | n={s['n']} full={s['full_avg']:+.3f}R "
        f"test_n={s['test_n']} test={s['test_avg']:+.3f}R",
        flush=True,
    )
    return trades, rpath, s


def main() -> None:
    from backtest_output.v65_revert_config import V5_SYMBOLS, UNIVERSE_57
    from backtest_output.v65_scale_in_backtest import run_scale_in
    import backtest_output.v6_path_backtest as bt

    avail = bt.klines_symbols()
    sym28 = [s for s in V5_SYMBOLS if s in avail]
    sym57 = [s for s in UNIVERSE_57 if s in avail]

    lines = [
        f"=== v65 BATCH RESEARCH {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC ===",
        f"symbols available: 28={len(sym28)} 57={len(sym57)}",
        "",
    ]
    summary_rows: list[dict] = []

    # ── 1. ws_merged fidelity (28, NY) ──
    _t, _r, s1 = run_capture_variant(
        label="ws_merged_28",
        symbols=sym28,
        session="ny",
        liq_source="ws_merged",
    )
    summary_rows.append({**s1, "batch": "1_fidelity"})
    lines.append(
        f"1 ws_merged_28: {s1['verdict']} n={s1['n']} full={s1['full_avg']:+.3f}R "
        f"test={s1['test_avg']:+.3f}R"
    )

    # Coinalyze rerun for matched trades+rpath (scale-in input)
    _t, _r, s_co = run_capture_variant(
        label="coinalyze_28",
        symbols=sym28,
        session="ny",
        liq_source="coinalyze",
    )
    summary_rows.append({**s_co, "batch": "1_baseline"})
    lines.append(
        f"1 coinalyze_28: {s_co['verdict']} n={s_co['n']} full={s_co['full_avg']:+.3f}R "
        f"test={s_co['test_avg']:+.3f}R"
    )
    co_trades_path = OUT / "v65_revert_trades.csv"
    if co_trades_path.exists():
        old = score(pd.read_csv(co_trades_path).to_dict("records"))
        lines.append(
            f"   (prior coinalyze run: n={old['n']} full={old['full_avg']:+.3f}R "
            f"test={old['test_avg']:+.3f}R)"
        )

    # ── 2. scale-in on coinalyze + ws_merged ──
    scale_rows: list[dict] = []
    for label, trades_name in [
        ("coinalyze", "v65_coinalyze_28_trades.csv"),
        ("ws_merged", "v65_ws_merged_28_trades.csv"),
    ]:
        trades_p = OUT / trades_name
        rpath_p = OUT / f"v6_bt_rpath_v645_{label}_28.csv"
        if not rpath_p.exists():
            alt = OUT / (f"v6_bt_rpath_v645_ws_merged.csv" if label == "ws_merged" else f"v6_bt_rpath_v645.csv")
            rpath_p = alt if alt.exists() else rpath_p
        if not trades_p.exists() or not rpath_p.exists():
            lines.append(f"2 scale_in_{label}: SKIP (missing trades or rpath)")
            continue
        res = run_scale_in(trades_p, rpath_p, label)
        for key in ("full", "scale"):
            row = {**res[key], "batch": f"2_scale_in_{label}", "base": key}
            scale_rows.append(row)
            summary_rows.append(row)
        sc = res["scale"]
        lines.append(
            f"2 scale_in_{label}: {sc['verdict']} full={res['full']['full_avg']:+.3f}R "
            f"scale={sc['full_avg']:+.3f}R test={sc['test_avg']:+.3f}R "
            f"scale_rate={sc['scale_rate']:.1%}"
        )

    # ── 3. session + universe sweeps (57 sym) ──
    for session in ("ny", "london", "asia"):
        label = f"universe57_{session}"
        _t, _r, s = run_capture_variant(
            label=label,
            symbols=sym57,
            session=session,
            liq_source="coinalyze",
        )
        summary_rows.append({**s, "batch": "3_universe"})
        lines.append(
            f"3 {label}: {s['verdict']} n={s['n']} full={s['full_avg']:+.3f}R "
            f"test={s['test_avg']:+.3f}R"
        )

    # Per-symbol OOS on ws_merged 28 (expansion candidates)
    ws_trades_p = OUT / "v65_ws_merged_28_trades.csv"
    if ws_trades_p.exists():
        td = pd.read_csv(ws_trades_p).sort_values("entry_time")
        split = int(len(td) * 0.6)
        test = td.iloc[split:]
        sym_oos = (
            test.groupby("symbol")["pnl_r"]
            .agg(n="count", avg="mean", tot="sum")
            .query("n >= 3")
            .sort_values("avg", ascending=False)
        )
        sym_oos.to_csv(OUT / "v65_batch_per_symbol_oos.csv")
        lines.extend(["", "Per-symbol OOS (ws_merged 28, n>=3):", sym_oos.head(15).to_string()])
        lines.extend(["", "Worst:", sym_oos.tail(5).to_string()])

    text = "\n".join(lines)
    (OUT / "v65_batch_results.txt").write_text(text + "\n")
    pd.DataFrame(summary_rows).to_csv(OUT / "v65_batch_summary.csv", index=False)
    (OUT / "v65_batch.done").write_text("done\n")

    print(f"\n{'='*60}\nBATCH COMPLETE\n{'='*60}", flush=True)
    print(text, flush=True)
    print(f"\nsaved v65_batch_results.txt, v65_batch_summary.csv", flush=True)


if __name__ == "__main__":
    main()
