"""Online layer: a shrunk multiplicative bias from our own resolved rounds.

Every round PowderBench resolves gives truth for what we actually submitted.
Per league (and per station where it has enough snow), ratio =
(sum truth + K) / (sum forecast + K) over the last N rounds, clipped. This is
what lets resorts-na / resorts — with no long truth archive to train on —
learn each resort's reporting habits, and keeps every league honest when
the season's regime drifts from training.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from .. import QCOLS, ROOT
from ..truth import resolved_rounds

# Our pre-bias forecasts, committed by the forecast workflow for every round
# we submit (history/<league>/<round>.csv: station_id, horizon_h, raw_in,
# snowfall_in, tier). The factor is fit on raw_in, so it is not compounded.
HISTORY = ROOT / "history"

LOOKBACK_ROUNDS = 30
K_LEAGUE, K_STATION = 15.0, 8.0
CLIP = (0.5, 2.0)


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
    h = our_history(league, before)
    if h.empty:
        return 1.0, {}
    h = h[h["horizon_h"] == 24]
    lg = (h["truth_in"].sum() + K_LEAGUE) / (h["snowfall_in"].sum() + K_LEAGUE)
    lg = min(max(lg, CLIP[0]), CLIP[1])
    g = h.groupby("station_id").agg(t=("truth_in", "sum"), f=("snowfall_in", "sum"))
    st = ((g["t"] + K_STATION * lg) / (g["f"] * lg + K_STATION)).clip(*CLIP)
    return float(lg), st.to_dict()


def apply(pred: pd.DataFrame, league: str, before: date) -> pd.DataFrame:
    lg, st = factors(league, before)
    if lg == 1.0 and not st:
        return pred
    f = lg * pred["station_id"].map(st).fillna(1.0)
    pred = pred.copy()
    for c in (*QCOLS, "snowfall_in"):
        pred[c] = (pred[c] * f).clip(0, 200)
    print(f"online bias {league}: league x{lg:.2f}, {len(st)} station factors")
    return pred
