"""Build one round's submission.

Featured stations get every model (they decide the headline score and the
ranking gate); the rest of the network gets a cheaper 2-model set once per
round and is carried over on later updates (Open-Meteo's free tier bills
per location). Tier 1 (the trained GBM) is used where its bundle exists and
inputs are complete; tier 0 fills every gap, so a file always goes out.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from . import HORIZONS, NETWORK_MODELS, NH_LEAGUES, OWN_BUNDLE_LEAGUES, PROXY_LEAGUE, QCOLS, ROOT
from .contract import Round, stations
from .dataset import feature_rows
from .features import daily
from .models import fallback, gbm, online
from .sources import openmeteo as om
from .store import MODELS

HISTORY = ROOT / "history"
COLS = ["station_id", "horizon_h", "snowfall_in", *QCOLS, "prob_6in"]


def bundle_names(league: str) -> list[str]:
    """Preference order: the league's own bundle, then its proxy's."""
    names = [league] if league in OWN_BUNDLE_LEAGUES else []
    base = PROXY_LEAGUE.get(league, league)
    return names + (["nh"] if base in NH_LEAGUES else [])


def load_bundle(league: str) -> tuple[str | None, gbm.Bundle | None]:
    for n in bundle_names(league):
        p = MODELS / f"{n}.pkl"
        if p.exists():
            return n, gbm.Bundle.load(p)
    return None, None


@dataclass
class Result:
    df: pd.DataFrame  # the submission
    raw: pd.DataFrame  # station_id, horizon_h, raw_in (pre online bias), tier
    info: dict


def _daily(r: Round, st: pd.DataFrame, models: tuple[str, ...] | None, ledger) -> pd.DataFrame:
    if st.empty:
        return pd.DataFrame()
    a, b = r.target_days[0], r.target_days[-1]
    if models is None:
        h = om.fetch_by_region(st, a, b, live=True, ledger=ledger)
    else:
        h = om.fetch_hourly(st, a, b, models, ledger=ledger, batch=50)
    print(f"  {r.key}: fetched {st.shape[0]} stations ({'all models' if models is None else models})", flush=True)
    return daily(h)


def _model(dly: pd.DataFrame, r: Round, as_league: str, bundle: gbm.Bundle | None) -> pd.DataFrame:
    """Tier 0 from every model fetched; tier 1 from only the models the
    bundle was trained on (so cross-model summaries mean the same thing).
    as_league: the league whose truth flavour the bundle should predict."""
    X = feature_rows(dly, r.league, live_round=r.round_date)
    if X.empty:
        return pd.DataFrame()
    t0 = fallback.predict(X)
    t0["tier"] = "t0"
    if bundle is None:
        return t0
    trained = {f.split("__", 1)[1] for f in bundle.features if f.startswith("slr__")}
    Xg = feature_rows(dly[dly["model"].isin(trained)], r.league, live_round=r.round_date)
    if Xg.empty:
        return t0
    Xg["league_code"] = NH_LEAGUES.index(as_league) if as_league in NH_LEAGUES else len(NH_LEAGUES)
    t1 = gbm.predict(bundle, Xg)
    t1["tier"] = "t1"
    ok = Xg["cons_slr"].notna().to_numpy() & (Xg["nmod"].to_numpy() >= 2)
    # beyond the training range a tree model can't extrapolate: lean on tier 0
    p99 = bundle.metrics.get("cons_slr_p99", {})
    lim = Xg["horizon_h"].map(lambda h: p99.get(str(int(h)), np.inf)).to_numpy()
    extreme = Xg["cons_slr"].to_numpy() > lim
    t1 = t1[ok].copy()
    ext = extreme[ok]
    if ext.any():
        t0i = t0.set_index(["station_id", "horizon_h"])
        for c in (*QCOLS, "snowfall_in"):
            base = t0i[c].reindex(pd.MultiIndex.from_frame(t1.loc[ext, ["station_id", "horizon_h"]])).to_numpy()
            t1.loc[ext, c] = np.fmax(t1.loc[ext, c].to_numpy(), 0.5 * (t1.loc[ext, c].to_numpy() + base))
        t1.loc[ext, "tier"] = "t1x"
    return pd.concat([t1, t0]).drop_duplicates(["station_id", "horizon_h"], keep="first")


def _carry_over(league: str, d, ids: set[str]) -> pd.DataFrame:
    p = HISTORY / league / f"{d.isoformat()}.csv"
    if not p.exists():
        return pd.DataFrame()
    prev = pd.read_csv(p)
    return prev[prev["station_id"].isin(ids)]


FEATURED_MINUTES = 15  # time box for the featured fetch (they decide the ranking)
NETWORK_MINUTES = 10  # time box for the rest of the network


def build(r: Round, network: bool = True, budget: float | None = None) -> Result:
    import time

    ledger = om.Ledger(budget=budget, deadline=time.time() + FEATURED_MINUTES * 60)
    used0 = ledger.used
    reg = stations(r.league)
    ids = [s for s in r.stations if s in reg.index]
    feat_ids = [s for s in r.featured if s in reg.index]
    net_ids = [s for s in ids if s not in set(feat_ids)]
    bname, bundle = load_bundle(r.league)
    info = {"round": r.key, "bundle": bname, "quota_note": None}

    proxy = r.league if bname == r.league else PROXY_LEAGUE.get(r.league, r.league)
    parts = [_model(_daily(r, reg.loc[feat_ids], None, ledger), r, proxy, bundle)]

    carried = _carry_over(r.league, r.round_date, set(net_ids)) if network else pd.DataFrame()
    todo = sorted(set(net_ids) - set(carried.get("station_id", [])))
    if network and todo:
        ledger.deadline = time.time() + NETWORK_MINUTES * 60
        try:
            parts.append(_model(_daily(r, reg.loc[todo], NETWORK_MODELS, ledger), r, proxy, bundle))
        except om.QuotaExhausted as e:  # featured rows are what matter: ship without the rest
            info["quota_note"] = f"network rows skipped: {e}"
    parts = [p for p in parts if len(p)]
    pred = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=[*COLS, "tier"])
    pred["raw_in"] = pred["snowfall_in"]
    pred = online.apply(pred, r.league, r.round_date)
    if len(carried):
        keep = carried[~carried["station_id"].isin(pred["station_id"])]
        pred = pd.concat([pred, keep], ignore_index=True)
    df = sanitize(pred)
    raw = pred[["station_id", "horizon_h", "raw_in", "snowfall_in", *QCOLS, "prob_6in", "tier"]]
    fe = set(feat_ids)
    info.update(
        rows=len(df),
        featured_coverage=round(df[df.station_id.isin(fe)].shape[0] / max(1, 3 * len(fe)), 3),
        tiers=raw["tier"].value_counts().to_dict(),
        openmeteo_weight=round(ledger.used - used0, 1),
    )
    return Result(df=df, raw=raw, info=info)


def sanitize(pred: pd.DataFrame) -> pd.DataFrame:
    """Exactly the PowderBench schema: numeric, clipped, monotone quantiles,
    p50 == snowfall_in, prob_6in only on 24 h rows, no duplicates."""
    df = pred[["station_id", "horizon_h", "snowfall_in", *QCOLS, "prob_6in"]].copy()
    df = df[df["horizon_h"].isin(HORIZONS)].dropna(subset=["snowfall_in"])
    df = df.drop_duplicates(["station_id", "horizon_h"])
    q = df[list(QCOLS)].apply(pd.to_numeric, errors="coerce")
    q["p50"] = df["snowfall_in"]
    for c in QCOLS:
        q[c] = q[c].fillna(q["p50"])
    arr = np.sort(np.clip(q.to_numpy(dtype=float), 0, 200), axis=1)
    df[list(QCOLS)] = np.round(arr, 2)
    df["snowfall_in"] = df["p50"]
    # cumulative horizons can't shrink: enforce h24 <= h48 <= h72 per station
    df = df.sort_values(["station_id", "horizon_h"])
    for c in (*QCOLS, "snowfall_in"):
        df[c] = df.groupby("station_id")[c].cummax()
    p = pd.to_numeric(df["prob_6in"], errors="coerce").clip(0.001, 0.999).round(3)
    df["prob_6in"] = p.where(df["horizon_h"] == 24)
    return df.reset_index(drop=True)


def write(res: Result, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    res.df.to_csv(out, index=False)
    return out
