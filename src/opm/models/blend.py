"""Learned combination of our model with the public baselines.

A convex combination (weights >= 0, summing to 1) of our p50 and the public
`baseline-*` forecasts, fitted by mean absolute error on the featured rows of
our resolved rounds. Where a source is missing for a row its weight is spread
over the sources that are there.

Few rounds exist, so the fit is cautious twice over: weight only moves from
one source to another while each move buys at least MIN_STEP_GAIN of MAE, and
the result is shrunk toward the prior (all weight on our model) by the number
of resolved rounds.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SKIP = ("zeros", "climo", "persistence", "submitted", "sim", "truth")
STEP = 0.05
MIN_STEP_GAIN = 0.002
ROUND_K = 4.0  # rounds of pseudo-evidence for the prior
MIN_ROUNDS = 4
MIN_COVER = 0.5  # a source must cover this share of rows to be a candidate


def combine(X: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Row-wise weighted mean over the sources present (NaN = absent)."""
    ok = ~np.isnan(X)
    num = np.where(ok, X, 0.0) @ w
    den = ok @ w
    return np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)


def _mae(X, y, w, anchor):
    p = combine(X, w)
    p = np.where(np.isnan(p), anchor, p)
    return float(np.abs(p - y).mean())


def fit(df: pd.DataFrame, ours: str = "raw", round_k: float = ROUND_K, step_gain: float = MIN_STEP_GAIN,
        sources: tuple[str, ...] | None = None) -> dict[str, float] | None:
    """Weights by source from a replay frame (see `replay.load`)."""
    g = df.dropna(subset=[ours, "truth"])
    n_rounds = g["round_date"].nunique() if len(g) else 0
    if n_rounds < MIN_ROUNDS:
        return None
    cand = [c for c in g.columns if c not in (*SKIP, ours, "round_date", "station_id", "horizon_h")
            and (sources is None or c in sources) and g[c].notna().mean() >= MIN_COVER]
    cols = [ours, *cand]
    X, y = g[cols].to_numpy(float), g["truth"].to_numpy(float)
    anchor = X[:, 0]
    w = np.zeros(len(cols))
    w[0] = 1.0
    best = _mae(X, y, w, anchor)
    while True:
        move = None
        for i in np.flatnonzero(w >= STEP - 1e-9):
            for j in range(len(cols)):
                if i == j:
                    continue
                t = w.copy()
                t[i] -= STEP
                t[j] += STEP
                m = _mae(X, y, t, anchor)
                if m < best * (1 - step_gain) and (move is None or m < move[0]):
                    move = (m, t)
        if move is None:
            break
        best, w = move
    k = n_rounds / (n_rounds + round_k)
    w = k * w
    w[0] += 1 - k
    return {c: round(float(v), 4) for c, v in zip(cols, w) if v > 1e-9}


def predict(df: pd.DataFrame, weights: dict[str, float], ours: str = "raw") -> np.ndarray:
    cols = [c for c in weights if c in df]
    p = combine(df[cols].to_numpy(float), np.array([weights[c] for c in cols]))
    return np.where(np.isnan(p), df[ours].to_numpy(float), p)
