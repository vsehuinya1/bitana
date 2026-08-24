from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.feature_selection import mutual_info_classif
from sklearn.metrics import roc_auc_score

from research_duo.config_loader import PipelineConfig
from research_duo.experiments.entry_labels import enrich_entry_fields, load_labeled_cohort, phase5_settings
from research_duo.experiments.oos_common import cohens_d
from research_duo.phase3_io import load_r_path_long

CONFIRM_KEYS = ("breakout", "imb", "vol", "body", "impulse", "momentum")
COMBO_KEYS = (
    ("imb_vol", ("imb", "vol")),
    ("imb_momentum", ("imb", "momentum")),
    ("vol_breakout", ("vol", "breakout")),
    ("all_six", CONFIRM_KEYS),
)


def _subset_binary(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["entry_class"].isin(["EXPLOSIVE", "EARLY_DEAD"])].copy()


def _numeric_compare(df: pd.DataFrame, col: str) -> dict[str, Any]:
    sub = _subset_binary(df)
    exp = sub[sub["entry_class"] == "EXPLOSIVE"][col].dropna().astype(float)
    dead = sub[sub["entry_class"] == "EARLY_DEAD"][col].dropna().astype(float)
    return {
        "explosive_mean": float(exp.mean()) if len(exp) else None,
        "explosive_median": float(exp.median()) if len(exp) else None,
        "early_dead_mean": float(dead.mean()) if len(dead) else None,
        "early_dead_median": float(dead.median()) if len(dead) else None,
        "cohens_d": cohens_d(exp.to_numpy(), dead.to_numpy()),
        "explosive_n": len(exp),
        "early_dead_n": len(dead),
    }


def _categorical_compare(df: pd.DataFrame, col: str) -> dict[str, Any]:
    sub = _subset_binary(df)
    rows = []
    for cls in ("EXPLOSIVE", "EARLY_DEAD"):
        part = sub[sub["entry_class"] == cls]
        vc = part[col].value_counts(normalize=True).head(10)
        rows.append({"class": cls, "n": len(part), "distribution": vc.to_dict()})
    return {"by_class": rows}


def feature_comparison_table(df: pd.DataFrame) -> dict[str, Any]:
    enriched = enrich_entry_fields(df)
    table: dict[str, Any] = {}

    numeric_cols = {
        "decile": "entry_decile",
        "aggression_score": "aggression_score",
        "cascade_strength": "cascade_strength",
        "breakout_distance_pct": "bd_distance_pct",
        "imbalance_z": "imbalance_z",
        "vol_z": "vol_z",
    }
    for label, col in numeric_cols.items():
        if col in enriched.columns:
            table[label] = _numeric_compare(enriched, col)

    for key in CONFIRM_KEYS:
        col = f"confirm_{key}"
        if col in enriched.columns:
            table[f"confirm_{key}"] = {
                "explosive_rate": float(enriched.loc[enriched["entry_class"] == "EXPLOSIVE", col].mean())
                if (enriched["entry_class"] == "EXPLOSIVE").any()
                else None,
                "early_dead_rate": float(enriched.loc[enriched["entry_class"] == "EARLY_DEAD", col].mean())
                if (enriched["entry_class"] == "EARLY_DEAD").any()
                else None,
            }

    for cat_col, src in [
        ("session", "session"),
        ("tier_group", "tier_group"),
        ("is_experimental", "is_experimental"),
    ]:
        if src in enriched.columns:
            table[cat_col] = _categorical_compare(enriched, src)

    return table


def _bar_features(r_path: pd.DataFrame, cutoff: int) -> pd.DataFrame:
    rows = []
    for tuuid, g in r_path.groupby("trade_uuid"):
        early = g[g["bar_index"] <= cutoff].sort_values("bar_index")
        if early.empty:
            continue
        last = early.iloc[-1]
        rows.append(
            {
                "trade_uuid": tuuid,
                f"mfe_b{cutoff}": float(last["mfe_so_far"]),
                f"mae_b{cutoff}": float(last["mae_so_far"]),
                f"unrealized_r_b{cutoff}": float(last["unrealized_r"]),
                f"mfe_velocity_b{cutoff}": float(last["mfe_velocity"]),
            }
        )
    return pd.DataFrame(rows)


def _univariate_auc(y: np.ndarray, x: np.ndarray) -> float | None:
    if len(set(y)) < 2 or len(x) < 5:
        return None
    try:
        return float(roc_auc_score(y, x))
    except ValueError:
        return None


def earliest_separating_features(df: pd.DataFrame, config: PipelineConfig) -> dict[str, Any]:
    enriched = enrich_entry_fields(df)
    sub = _subset_binary(enriched)
    r_path = load_r_path_long(config)
    y = (sub["entry_class"] == "EXPLOSIVE").astype(int).to_numpy()

    entry_features = [
        "entry_decile",
        "aggression_score",
        "cascade_strength",
        "bd_distance_pct",
        "imbalance_z",
        "vol_z",
        "confirm_vol",
        "confirm_momentum",
        "confirm_impulse",
        "confirm_body",
        "confirm_imb",
        "confirm_breakout",
    ]

    results: dict[str, Any] = {}
    for horizon, cutoff in [("entry", 0), ("bar_3", 3), ("bar_5", 5)]:
        if cutoff == 0:
            frame = sub.copy()
            feat_cols = [c for c in entry_features if c in frame.columns]
        else:
            bf = _bar_features(r_path, cutoff)
            frame = sub.merge(bf, on="trade_uuid", how="inner")
            feat_cols = [c for c in entry_features if c in frame.columns] + [
                c for c in frame.columns if c.endswith(f"b{cutoff}")
            ]

        rows = []
        X_parts = []
        valid_cols = []
        for col in feat_cols:
            vals = pd.to_numeric(frame[col], errors="coerce")
            mask = vals.notna()
            if mask.sum() < 5:
                continue
            xv = vals.loc[mask].to_numpy(dtype=float)
            yv = frame.loc[mask, "entry_class"].eq("EXPLOSIVE").astype(int).to_numpy()
            d = cohens_d(xv[yv == 1], xv[yv == 0]) if yv.sum() > 0 and (1 - yv).sum() > 0 else None
            auc = _univariate_auc(yv, xv)
            rows.append(
                {
                    "feature": col,
                    "cohens_d": d,
                    "univariate_auc": auc,
                    "explosive_mean": float(xv[yv == 1].mean()) if yv.sum() else None,
                    "early_dead_mean": float(xv[yv == 0].mean()) if (1 - yv).sum() else None,
                }
            )
            X_parts.append(vals.fillna(vals.median()).to_numpy(dtype=float))
            valid_cols.append(col)

        mi_scores = {}
        if valid_cols and len(frame) >= 10:
            X = np.column_stack(X_parts)
            y_mi = frame["entry_class"].eq("EXPLOSIVE").astype(int).to_numpy()
            try:
                mi = mutual_info_classif(X, y_mi, random_state=int(phase5_settings(config).get("random_seed", 42)))
                mi_scores = {valid_cols[i]: float(mi[i]) for i in range(len(valid_cols))}
            except Exception:
                mi_scores = {}

        for row in rows:
            row["mutual_information"] = mi_scores.get(row["feature"])

        rows.sort(key=lambda r: abs(r.get("cohens_d") or 0), reverse=True)
        results[horizon] = {"features": rows, "n": len(frame)}

    return results


def confirmation_stack_analysis(df: pd.DataFrame) -> dict[str, Any]:
    enriched = enrich_entry_fields(df)
    out: dict[str, Any] = {"single": {}, "combinations": {}}

    def _rates(part: pd.DataFrame) -> dict[str, Any]:
        return {
            "n": len(part),
            "frequency": len(part) / len(enriched) if len(enriched) else 0,
            "win_rate": float((part["pnl_r"] > 0).mean()) if len(part) else None,
            "explosive_rate": float((part["entry_class"] == "EXPLOSIVE").mean()) if len(part) else None,
            "early_dead_rate": float((part["entry_class"] == "EARLY_DEAD").mean()) if len(part) else None,
        }

    for key in CONFIRM_KEYS:
        col = f"confirm_{key}"
        if col not in enriched.columns:
            continue
        out["single"][key] = _rates(enriched[enriched[col]])

    for combo_name, keys in COMBO_KEYS:
        mask = pd.Series(True, index=enriched.index)
        for k in keys:
            col = f"confirm_{k}"
            if col in enriched.columns:
                mask &= enriched[col]
        out["combinations"][combo_name] = _rates(enriched[mask])

    return out


def confirmation_integrity_qa(df: pd.DataFrame) -> dict[str, Any]:
    enriched = enrich_entry_fields(df)
    qa: dict[str, Any] = {}
    if "confirmations_mismatch" in enriched.columns:
        qa["confirmation_mismatch_rate"] = float(enriched["confirmations_mismatch"].mean())
    if "imbalance_z" in enriched.columns and "confirm_imb" in enriched.columns:
        qa["imb_zero_but_confirm_pass"] = int(
            ((enriched["imbalance_z"].fillna(0) == 0) & enriched["confirm_imb"]).sum()
        )
    qa["imb_fallback_rate"] = float(enriched["imb_fallback_flag"].mean()) if "imb_fallback_flag" in enriched.columns else None
    return qa


def breakout_expansion_analysis(df: pd.DataFrame, config: PipelineConfig) -> dict[str, Any]:
    enriched = enrich_entry_fields(df)
    r_path = load_r_path_long(config)
    settings = phase5_settings(config)
    bd_bins = settings.get("bd_buckets", [-999, -2, 0, 2, 999])
    mfe_bins = settings.get("mfe10_buckets", [0, 0.1, 0.3, 0.5, 999])

    first10 = []
    if "max_mfe_first_10" not in enriched.columns:
        for tuuid, g in r_path.groupby("trade_uuid"):
            early = g[g["bar_index"] <= 10]
            if early.empty:
                continue
            first10.append({"trade_uuid": tuuid, "max_mfe_first_10": float(early["mfe_so_far"].max())})
        merged = enriched.merge(pd.DataFrame(first10), on="trade_uuid", how="left")
    else:
        merged = enriched.copy()

    mfe_col = "max_mfe_first_10"
    if mfe_col not in merged.columns:
        return {"bd_bucket_table": [], "bd_x_mfe10_cross": [], "available": False}
    merged["bd_bucket"] = pd.cut(
        merged["bd_distance_pct"],
        bins=bd_bins,
        labels=["<-2%", "-2to0%", "0to2%", ">2%"],
    )
    merged["mfe10_bucket"] = pd.cut(
        merged[mfe_col],
        bins=mfe_bins,
        labels=["0-0.1R", "0.1-0.3R", "0.3-0.5R", ">0.5R"],
    )

    bd_rows = []
    for bucket, grp in merged.groupby("bd_bucket", observed=True):
        bd_rows.append(
            {
                "bd_bucket": str(bucket),
                "n": len(grp),
                "explosive_rate": float((grp["entry_class"] == "EXPLOSIVE").mean()),
                "early_dead_rate": float((grp["entry_class"] == "EARLY_DEAD").mean()),
                "mean_first10_mfe": float(grp[mfe_col].mean()) if grp[mfe_col].notna().any() else None,
                "mean_eventual_mfe": float(grp["max_mfe"].mean()) if "max_mfe" in grp.columns else None,
            }
        )

    cross = []
    for (bd, mfe), grp in merged.groupby(["bd_bucket", "mfe10_bucket"], observed=True):
        cross.append(
            {
                "bd_bucket": str(bd),
                "mfe10_bucket": str(mfe),
                "n": len(grp),
                "explosive_rate": float((grp["entry_class"] == "EXPLOSIVE").mean()) if len(grp) else None,
            }
        )

    return {"bd_bucket_table": bd_rows, "bd_x_mfe10_cross": cross}


def cascade_quality_analysis(df: pd.DataFrame) -> dict[str, Any]:
    enriched = enrich_entry_fields(df)
    if "cascade_strength" not in enriched.columns:
        return {"available": False}

    enriched = enriched[enriched["cascade_strength"].notna()].copy()
    enriched["cascade_decile"] = pd.qcut(
        enriched["cascade_strength"],
        q=min(10, len(enriched)),
        duplicates="drop",
    )

    rows = []
    for decile, grp in enriched.groupby("cascade_decile", observed=True):
        rows.append(
            {
                "cascade_decile": str(decile),
                "n": len(grp),
                "cascade_strength_mean": float(grp["cascade_strength"].mean()),
                "explosive_rate": float((grp["entry_class"] == "EXPLOSIVE").mean()),
                "early_dead_rate": float((grp["entry_class"] == "EARLY_DEAD").mean()),
                "win_rate": float((grp["pnl_r"] > 0).mean()),
            }
        )
    return {"decile_table": rows, "n": len(enriched)}


def symbol_tier_confound(df: pd.DataFrame) -> dict[str, Any]:
    enriched = enrich_entry_fields(df)
    by_symbol = (
        enriched.groupby("symbol")["entry_class"]
        .value_counts()
        .unstack(fill_value=0)
        .reset_index()
    )
    by_tier = (
        enriched.groupby("tier_group")["entry_class"]
        .value_counts()
        .unstack(fill_value=0)
        .reset_index()
    )
    return {
        "by_symbol": by_symbol.to_dict(orient="records"),
        "by_tier": by_tier.to_dict(orient="records"),
    }


def class_counts(df: pd.DataFrame) -> dict[str, int]:
    return df["entry_class"].value_counts().to_dict()


def run_entry_quality(config: PipelineConfig) -> dict[str, Any]:
    settings = phase5_settings(config)
    results: dict[str, Any] = {
        "label_definitions": {
            "EXPLOSIVE": "pnl_r > 0 AND mfe >= 1.0R within 15 bars",
            "EARLY_DEAD": "pnl_r <= 0 AND max mfe in bars 1-10 <= 0.3R",
            "SURVIVOR": "pnl_r > 0 AND NOT EXPLOSIVE",
            "LATE_DEAD": "pnl_r <= 0 AND max mfe in bars 1-10 > 0.3R",
        },
        "cohorts": {},
    }

    for cohort_name, post_gate in [("all_r_path", False), ("post_gate", True)]:
        df = load_labeled_cohort(config, post_gate_only=post_gate)
        enriched = enrich_entry_fields(df)
        results["cohorts"][cohort_name] = {
            "n": len(df),
            "class_counts": class_counts(df),
            "confirmation_integrity": confirmation_integrity_qa(df),
            "feature_comparison": feature_comparison_table(df),
            "earliest_separation": earliest_separating_features(df, config),
            "confirmation_stack": confirmation_stack_analysis(df),
            "breakout_expansion": breakout_expansion_analysis(df, config),
            "cascade_quality": cascade_quality_analysis(df),
            "symbol_tier_confound": symbol_tier_confound(df),
        }

    return results


_COMPRESSION_FEATURES = (
    "vol_z",
    "aggression_score",
    "confirm_vol",
    "confirm_impulse",
    "confirm_momentum",
    "bd_distance_pct",
)


def _compression_matrix(df: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray, list[str]]:
    enriched = enrich_entry_fields(df)
    sub = enriched[enriched["entry_class"].isin(["EXPLOSIVE", "EARLY_DEAD"])].copy()
    cols = [c for c in _COMPRESSION_FEATURES if c in sub.columns]
    X = sub[cols].apply(pd.to_numeric, errors="coerce")
    for col in cols:
        if X[col].dtype == bool or set(X[col].dropna().unique()).issubset({0, 1, True, False}):
            X[col] = X[col].astype(float)
    mask = X.notna().all(axis=1)
    sub = sub.loc[mask].reset_index(drop=True)
    X = X.loc[mask].to_numpy(dtype=float)
    mu = X.mean(axis=0)
    sigma = X.std(axis=0, ddof=0)
    sigma[sigma == 0] = 1.0
    Xz = (X - mu) / sigma
    return sub, Xz, cols


def _auc_binary(y: np.ndarray, scores: np.ndarray) -> float | None:
    if len(set(y)) < 2 or len(y) < 5:
        return None
    try:
        return float(roc_auc_score(y, scores))
    except ValueError:
        return None


def _overlap_pct(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 3 or len(b) < 3:
        return None
    lo = float(min(a.min(), b.min()))
    hi = float(max(a.max(), b.max()))
    if hi <= lo:
        return None
    bins = np.linspace(lo, hi, 11)
    ha, _ = np.histogram(a, bins=bins)
    hb, _ = np.histogram(b, bins=bins)
    pa = ha / ha.sum()
    pb = hb / hb.sum()
    return float(np.minimum(pa, pb).sum())


def _compression_verdict(
    n_exp: int,
    n_dead: int,
    pca_d: float | None,
    log_auc: float | None,
    log_d: float | None,
    vol_auc: float | None,
    pc1_var: float | None,
) -> tuple[str, str]:
    if n_exp < 8 or n_dead < 8:
        return "INCONCLUSIVE", f"Underpowered (EXPLOSIVE n={n_exp}, EARLY_DEAD n={n_dead})."

    if log_auc is not None and log_auc >= 0.65 and log_d is not None and abs(log_d) >= 0.5:
        if vol_auc is not None and log_auc - vol_auc <= 0.05:
            return (
                "KILLED",
                "Logistic axis separates classes but vol_z alone matches within 0.05 AUC — "
                "no evidence of a multi-feature latent axis beyond volume participation.",
            )
        return (
            "CONFIRMED",
            "Multi-feature logistic score shows material separation (AUC≥0.65, |d|≥0.5) "
            "and beats vol_z alone by >0.05 AUC.",
        )

    if log_auc is not None and log_auc < 0.58:
        return (
            "KILLED",
            "Logistic axis AUC < 0.58 — no meaningful EXPLOSIVE vs EARLY_DEAD separation.",
        )

    if pca_d is not None and abs(pca_d) < 0.35 and pc1_var is not None and pc1_var < 0.25:
        return (
            "KILLED",
            f"PC1 explains {pc1_var:.0%} variance with |d|={abs(pca_d):.2f} — features do not collapse to one axis.",
        )

    return "INCONCLUSIVE", "Separation present but weak or single-feature dominated; sample too small for firm call."


def run_entry_compression_test(config: PipelineConfig) -> dict[str, Any]:
    """Single-shot test: does a latent participation axis separate EXPLOSIVE vs EARLY_DEAD at entry?"""
    from sklearn.decomposition import PCA
    from sklearn.linear_model import LogisticRegression

    from research_duo.paths import REPO_ROOT

    cohorts_out: dict[str, Any] = {}
    for cohort_name, post_gate in [("post_gate", True), ("all_r_path", False)]:
        df = load_labeled_cohort(config, post_gate_only=post_gate)
        sub, Xz, cols = _compression_matrix(df)
        y = (sub["entry_class"] == "EXPLOSIVE").astype(int).to_numpy()
        n_exp = int(y.sum())
        n_dead = int(len(y) - n_exp)

        pca = PCA(n_components=min(2, Xz.shape[1], len(y)))
        scores_pca = pca.fit_transform(Xz)
        pc1 = scores_pca[:, 0]
        pc1_var = float(pca.explained_variance_ratio_[0]) if len(pca.explained_variance_ratio_) else None
        loadings = {cols[i]: float(pca.components_[0, i]) for i in range(len(cols))}

        lr = LogisticRegression(C=1.0, max_iter=500, random_state=42)
        lr.fit(Xz, y)
        log_score = lr.predict_proba(Xz)[:, 1]

        vol_idx = cols.index("vol_z") if "vol_z" in cols else None
        vol_scores = Xz[:, vol_idx] if vol_idx is not None else np.zeros(len(y))

        pca_d = cohens_d(pc1[y == 1], pc1[y == 0])
        log_d = cohens_d(log_score[y == 1], log_score[y == 0])
        vol_d = cohens_d(vol_scores[y == 1], vol_scores[y == 0]) if vol_idx is not None else None

        log_auc = _auc_binary(y, log_score)
        vol_auc = _auc_binary(y, vol_scores) if vol_idx is not None else None

        verdict, rationale = _compression_verdict(
            n_exp, n_dead, pca_d, log_auc, log_d, vol_auc, pc1_var
        )

        cohorts_out[cohort_name] = {
            "n_total": len(sub),
            "n_explosive": n_exp,
            "n_early_dead": n_dead,
            "features": cols,
            "pc1_variance_explained": pc1_var,
            "pc1_loadings": loadings,
            "cohens_d": {"pca1": pca_d, "logistic": log_d, "vol_z": vol_d},
            "auc": {"pca1": _auc_binary(y, pc1), "logistic": log_auc, "vol_z": vol_auc},
            "distribution_overlap": {
                "pca1": _overlap_pct(pc1[y == 1], pc1[y == 0]),
                "logistic": _overlap_pct(log_score[y == 1], log_score[y == 0]),
            },
            "verdict": verdict,
            "rationale": rationale,
        }

    primary = cohorts_out["post_gate"]
    lines = [
        "# Entry Compression Test",
        "",
        "Single measurement: does a latent participation axis separate EXPLOSIVE vs EARLY_DEAD at entry?",
        "Read-only. No threshold tuning. Pre-registered features only.",
        "",
        f"## Primary verdict (`post_gate`): **{primary['verdict']}**",
        "",
        primary["rationale"],
        "",
        f"N={primary['n_total']} (EXPLOSIVE={primary['n_explosive']}, EARLY_DEAD={primary['n_early_dead']})",
        "",
        "| Axis | Cohen's d | AUC | Overlap |",
        "| --- | --- | --- | --- |",
    ]
    for axis in ("pca1", "logistic", "vol_z"):
        d = primary["cohens_d"].get(axis)
        auc = primary["auc"].get(axis)
        ov = primary["distribution_overlap"].get(axis) if axis != "vol_z" else None
        d_s = f"{d:+.2f}" if d is not None else "n/a"
        auc_s = f"{auc:.3f}" if auc is not None else "n/a"
        ov_s = f"{ov:.0%}" if ov is not None else "—"
        lines.append(f"| {axis} | {d_s} | {auc_s} | {ov_s} |")

    if primary["pc1_variance_explained"] is not None:
        lines.extend(["", f"PC1 variance explained: {primary['pc1_variance_explained']:.1%}", ""])
    else:
        lines.extend(["", "PC1 variance explained: n/a", ""])

    lines.append("**PC1 loadings:**")
    for feat, loading in sorted(primary["pc1_loadings"].items(), key=lambda x: abs(x[1]), reverse=True):
        lines.append(f"- `{feat}`: {loading:+.3f}")

    sec = cohorts_out["all_r_path"]
    log_auc_s = f"{sec['auc']['logistic']:.3f}" if sec["auc"]["logistic"] is not None else "n/a"
    vol_auc_s = f"{sec['auc']['vol_z']:.3f}" if sec["auc"]["vol_z"] is not None else "n/a"
    log_d = sec["cohens_d"]["logistic"]
    log_d_s = f"{log_d:+.2f}" if log_d is not None else "n/a"
    lines.extend([
        "",
        "## Confirmatory (`all_r_path`)",
        "",
        f"**{sec['verdict']}** — N={sec['n_total']} "
        f"(EXP={sec['n_explosive']}, DEAD={sec['n_early_dead']}); "
        f"logistic AUC={log_auc_s}, vol_z AUC={vol_auc_s}, d_logistic={log_d_s}",
    ])

    report_dir = REPO_ROOT / "research_duo" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "entry_compression_test.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {"report": str(report_path), "cohorts": cohorts_out, "primary_verdict": primary["verdict"]}
