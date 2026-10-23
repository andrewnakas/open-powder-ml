"""Tier 0: the always-available floor (no training needed).

Point = median across models of each model's snow (the mean of its own
snowfall and our temperature-based snow-physics estimate). Quantiles = the
spread across models, widened because a raw multi-model spread is
overconfident. P(>= 6") = a smoothed share of models over 6".
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import QCOLS

WIDEN = {"p10": 0.55, "p25": 0.8, "p50": 1.0, "p75": 1.2, "p90": 1.5}


def per_model(W: pd.DataFrame) -> pd.DataFrame:
    models = sorted({c.split("__", 1)[1] for c in W.columns if c.startswith("slr__")})
    vals = {}
    for m in models:
        s, k = W.get(f"slr__{m}"), W.get(f"snow__{m}")
        vals[m] = pd.concat([s, k], axis=1).mean(axis=1) if k is not None else s
    return pd.DataFrame(vals, index=W.index)


def predict(W: pd.DataFrame) -> pd.DataFrame:
    v = per_model(W)
    arr = v.to_numpy(dtype=float)
    ok = np.isfinite(arr).sum(axis=1) > 0
    q = np.full((len(W), 5), np.nan)
    if ok.any():
        q[ok] = np.nanpercentile(arr[ok], [10, 25, 50, 75, 90], axis=1).T
    out = pd.DataFrame(q, columns=list(QCOLS), index=W.index)
    for c in QCOLS:
        out[c] = out[c] * WIDEN[c]
    out = pd.DataFrame(np.sort(out.clip(0, 200).to_numpy(), axis=1), columns=list(QCOLS), index=W.index)
    out["snowfall_in"] = out["p50"]
    n = np.isfinite(arr).sum(axis=1)
    over = (np.nan_to_num(arr, nan=-1) >= 6).sum(axis=1)
    out["prob_6in"] = (over + 0.2) / (n + 1.0)
    keys = W[["station_id", "round_date", "horizon_h"]]
    return pd.concat([keys, out], axis=1)[ok]
