"""Stacking on the public baselines (never on other competitors).

PowderBench's baselines (`baseline-*`: NBM, HRRR, WPC percentiles, NDFD
forecaster grids, model consensus, ...) are merged into each round a few
hours before the cutoff, so our later update passes can see them. From our
own resolved rounds this layer learns, per league:

- the single baseline partner that best complements our model, and
- a blend weight w (on a grid) for p50, minimising MAE on past rounds.

It only switches on with enough history (MIN_ROUNDS) and a real gain
(MIN_GAIN), and only where the partner covers the station. Quantiles are
blended when the partner has them (WPC); otherwise ours are rescaled by the
p50 change. P(>=6") blends the same way where the partner reports it.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .. import QCOLS, ROOT, pb_checkout
from ..contract import stations
from ..truth import resolved_rounds

HISTORY = ROOT / "history"
SKIP = ("baseline-zeros", "baseline-climatology", "baseline-persistence")
LOOKBACK = 40
MIN_ROUNDS = 8
MIN_GAIN = 0.02
GRID = np.round(np.arange(0.0, 0.81, 0.1), 2)  # partner weight; ours keeps >= 0.2


def _baselines(league: str, d: date) -> dict[str, pd.DataFrame]:
    out = {}
    for p in sorted((pb_checkout() / "data" / "submissions" / league / d.isoformat()).glob("baseline-*.csv")):
        if p.stem in SKIP:
            continue
        try:
            out[p.stem] = pd.read_csv(p).set_index(["station_id", "horizon_h"])
        except Exception:
            continue
    return out


def _featured(league: str) -> set[str]:
    st = stations(league)
    return set(st.index[st["featured"]])


def fit(league: str, before: date) -> dict | None:
    tr = resolved_rounds(league)
    tr = tr[tr["round_date"] < before]
    # the headline board is scored on the featured stations, so that is where
    # the partner and the weight are chosen (network rows outnumber them 7:1)
    feat = _featured(league)
    if feat:
        tr = tr[tr["station_id"].isin(feat)]
    rows = []
    for d in sorted(tr["round_date"].unique())[-LOOKBACK:]:
        h = HISTORY / league / f"{d.isoformat()}.csv"
        if not h.exists():
            continue
        ours = pd.read_csv(h)
        if "raw_in" not in ours:
            continue
        t = tr[tr["round_date"] == d].set_index(["station_id", "horizon_h"])["truth_in"]
        # the model's own output, before the online layer: that layer's early
        # version scaled five stations rounds by 4x, and a weight learned from
        # those rows would describe that bug, not the model
        o = ours.set_index(["station_id", "horizon_h"])["raw_in"]
        for name, b in _baselines(league, d).items():
            j = pd.concat([o.rename("ours"), b["snowfall_in"].rename("base"), t.rename("truth")], axis=1).dropna()
            if len(j):
                rows.append(j.assign(partner=name, round=d))
    if not rows:
        return None
    df = pd.concat(rows)
    best = None
    for name, g in df.groupby("partner"):
        if g["round"].nunique() < MIN_ROUNDS:
            continue
        mae0 = (g["ours"] - g["truth"]).abs().mean()
        maes = [((1 - w) * g["ours"] + w * g["base"] - g["truth"]).abs().mean() for w in GRID]
        i = int(np.argmin(maes))
        gain = 1 - maes[i] / mae0 if mae0 else 0.0
        if GRID[i] > 0 and gain >= MIN_GAIN and (best is None or gain > best["gain"]):
            best = {"partner": name, "w": float(GRID[i]), "gain": float(gain), "rounds": int(g["round"].nunique())}
    return best


def apply(pred: pd.DataFrame, league: str, d: date) -> tuple[pd.DataFrame, dict | None]:
    cfg = fit(league, d)
    if not cfg:
        return pred, None
    base = _baselines(league, d).get(cfg["partner"])
    if base is None:
        return pred, {**cfg, "applied": 0}
    w = cfg["w"]
    p = pred.set_index(["station_id", "horizon_h"]).copy()
    b = base.reindex(p.index)
    has = b["snowfall_in"].notna()
    new50 = (1 - w) * p["snowfall_in"] + w * b["snowfall_in"]
    if all(c in b for c in QCOLS):
        for c in QCOLS:
            ok = has & b[c].notna()
            p.loc[ok, c] = (1 - w) * p.loc[ok, c] + w * b.loc[ok, c]
    else:
        ratio = (new50 / p["snowfall_in"].where(p["snowfall_in"] > 0.05)).clip(0.5, 2.0).fillna(1.0)
        for c in QCOLS:
            p.loc[has, c] = p.loc[has, c] * ratio[has]
    p.loc[has, "snowfall_in"] = new50[has]
    p.loc[has, "p50"] = new50[has]
    if "prob_6in" in b:
        ok = has & b["prob_6in"].notna() & p["prob_6in"].notna()
        p.loc[ok, "prob_6in"] = (1 - w) * p.loc[ok, "prob_6in"] + w * b.loc[ok, "prob_6in"]
    print(f"stack {league}: {cfg['partner']} w={w} (gain {cfg['gain']:.1%} over {cfg['rounds']} rounds), "
          f"{int(has.sum())} rows", flush=True)
    return p.reset_index(), {**cfg, "applied": int(has.sum())}
