"""PowderBench-style scoring for backtests (mirrors powderbench.scoring):
Powder Score = 100 * (1 - MAE / MAE_climatology) on rows where climatology
exists; pinball over the five quantiles; Brier on 24 h P(>= 6")."""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import POWDER_IN, QCOLS, QUANTILES


def powder_score(mae: float, mae_climo: float) -> float | None:
    return None if not mae_climo else round(100 * (1 - mae / mae_climo), 2)


def score(df: pd.DataFrame) -> dict:
    out: dict = {"n": len(df)}
    y = df["truth_in"]
    out["mae"] = float((df["snowfall_in"] - y).abs().mean())
    has_climo = "climo_p50" in df
    c = df.dropna(subset=["climo_p50"]) if has_climo else df.iloc[:0]
    if len(c):
        mc = float((c["climo_p50"] - c["truth_in"]).abs().mean())
        out["powder_score"] = powder_score(float((c["snowfall_in"] - c["truth_in"]).abs().mean()), mc)
        if "cons_slr" in c:
            cc = c.dropna(subset=["cons_slr"])
            out["powder_score_raw_consensus"] = powder_score(
                float((cc["cons_slr"] - cc["truth_in"]).abs().mean()),
                float((cc["climo_p50"] - cc["truth_in"]).abs().mean()))
    if all(q in df for q in QCOLS):
        loss = 0.0
        for a, q in zip(QUANTILES, QCOLS):
            d = y - df[q]
            loss += np.maximum(a * d, (a - 1) * d).mean()
        out["pinball"] = float(loss / len(QUANTILES))
    h24 = df[(df["horizon_h"] == 24) & df["prob_6in"].notna()] if "prob_6in" in df else df.iloc[:0]
    if len(h24):
        hit = (h24["truth_in"] >= POWDER_IN).astype(float)
        out["brier"] = float(((h24["prob_6in"] - hit) ** 2).mean())
        if "climo_p6" in h24:
            k = h24.dropna(subset=["climo_p6"])
            out["brier_climo"] = float(((k["climo_p6"] - (k["truth_in"] >= POWDER_IN)) ** 2).mean())
    for h in (24, 48, 72) if has_climo else ():
        s = df[(df["horizon_h"] == h)].dropna(subset=["climo_p50"])
        if len(s):
            out[f"powder_score_h{h}"] = powder_score(
                float((s["snowfall_in"] - s["truth_in"]).abs().mean()),
                float((s["climo_p50"] - s["truth_in"]).abs().mean()))
    return out
