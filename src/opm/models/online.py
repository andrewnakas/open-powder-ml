"""Online layer: a shrunk multiplicative correction from our own resolved rounds.

Every round PowderBench resolves gives truth for what our model said. Per
league, the factor is the multiplier of the model's own output (`raw_in`) that
would have given the lowest mean absolute error over the last N rounds, which
is what Powder Accuracy scores.

It is not the ratio of total truth to total forecast. A median forecast of
snowfall sits well below the mean on purpose (most days are dry, a few are
big), so that ratio is far above 1 even for a forecast no multiplier can
improve: on the first 13 stations rounds it was 1.95 while the error-minimising
factor was 1.15, on observers 1.60 against 0.80. Two earlier versions used it;
the first sent every stations forecast out at 4x the model for the
10-31..11-04 rounds, the second at 1.5x.

It is deliberately slow to act: off until MIN_ROUNDS resolved rounds, then
shrunk toward 1 by the number of rounds and clipped. Walking forward over the
resolved rounds (scripts/replay.py) no version of this layer has yet beaten the
model's own output, and per-station factors did no better, so there are none.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .. import QCOLS, ROOT
from ..truth import resolved_rounds

# Our pre-bias forecasts, committed by the forecast workflow for every round
# we submit (history/<league>/<round>.csv: station_id, horizon_h, raw_in,
# snowfall_in, tier). The factor is fit on raw_in, so it is not compounded.
HISTORY = ROOT / "history"

LOOKBACK_ROUNDS = 30
MIN_ROUNDS = 8  # resolved rounds of ours before any correction
ROUND_K = 20.0  # rounds of pseudo-evidence for "no correction"
CLIP = (0.67, 1.5)
GRID = np.round(np.arange(0.6, 1.601, 0.05), 2)


def our_history(league: str, before: date) -> pd.DataFrame:
    tr = resolved_rounds(league)
    tr = tr[tr["round_date"] < before]
    dates = sorted(tr["round_date"].unique())[-LOOKBACK_ROUNDS:]
    frames = []
    for d in dates:
        p = HISTORY / league / f"{d.isoformat()}.csv"
        if p.exists():
            s = pd.read_csv(p)[["station_id", "horizon_h", "raw_in"]].rename(columns={"raw_in": "snowfall_in"})
            s["round_date"] = d
            frames.append(s)
    if not frames:
        return pd.DataFrame()
    sub = pd.concat(frames, ignore_index=True)
    return sub.merge(tr, on=["station_id", "round_date", "horizon_h"])


def factors(league: str, before: date) -> tuple[float, dict[str, float]]:
    """(league factor, per-station factors). The station part is empty: kept
    in the signature for callers and for when the record supports it."""
    h = our_history(league, before)
    if h.empty:
        return 1.0, {}
    n = h["round_date"].nunique()
    if n < MIN_ROUNDS:
        return 1.0, {}
    f, t = h["snowfall_in"].to_numpy(float), h["truth_in"].to_numpy(float)
    lg = float(GRID[int(np.argmin([np.abs(f * x - t).sum() for x in GRID]))])
    lg = 1.0 + (lg - 1.0) * n / (n + ROUND_K)
    return float(min(max(lg, CLIP[0]), CLIP[1])), {}


def apply(pred: pd.DataFrame, league: str, before: date) -> pd.DataFrame:
    lg, st = factors(league, before)
    if lg == 1.0 and not st:
        return pred
    f = (lg * pred["station_id"].map(st).fillna(1.0)).clip(*CLIP)
    pred = pred.copy()
    for c in (*QCOLS, "snowfall_in"):
        pred[c] = (pred[c] * f).clip(0, 200)
    print(f"online bias {league}: league x{lg:.2f}, {len(st)} station factors")
    return pred
