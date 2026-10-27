"""Training table: archive features joined to PowderBench truth windows."""

from __future__ import annotations

from datetime import date

import pandas as pd

from . import NETWORK_MODELS, features, store, truth
from .contract import climatology, stations

SH_LEAGUES = ("era5", "resorts")
HF_LEAGUES = ("stations", "observers", "alps")


def season_of(d: pd.Series, league: str) -> pd.Series:
    d = pd.to_datetime(d)
    if league in SH_LEAGUES:
        return d.dt.year
    return d.dt.year.where(d.dt.month >= 8, d.dt.year - 1)


def feature_rows(dly: pd.DataFrame, league: str, live_round: date | None = None) -> pd.DataFrame:
    win = features.live_windows(dly, live_round) if live_round else features.archive_windows(dly)
    if win.empty:
        return pd.DataFrame()
    w = features.wide(win)
    return features.add_static(w, stations(league), climatology(league), league)


def training_table(league: str) -> pd.DataFrame:
    dly = store.load_daily(league)
    if dly.empty:
        return pd.DataFrame()
    X = feature_rows(dly, league)
    if league == "era5":
        st = stations(league)
        tw = truth.windows(truth.era5_daily(st, min(dly["date"]), max(dly["date"])))
    elif league in HF_LEAGUES:
        tw = truth.windows(truth.hf_daily(league, begin=min(dly["date"])))
    else:  # resort leagues: PowderBench's own resolved rounds are the archive
        tw = truth.resolved_rounds(league)
    df = X.merge(tw, on=["station_id", "round_date", "horizon_h"], how="inner")
    df["aug"] = 0
    # the same cases seen through only the network model subset (half of them)
    # (only leagues with non-featured network stations need it)
    Xs = feature_rows(dly[dly["model"].isin(NETWORK_MODELS)], league) if league in HF_LEAGUES else pd.DataFrame()
    if len(Xs):
        sub = Xs.merge(tw, on=["station_id", "round_date", "horizon_h"], how="inner")
        df = pd.concat([df, sub.sample(frac=0.5, random_state=0).assign(aug=1)], ignore_index=True)
    df["league"] = league
    df["season"] = season_of(df["round_date"], league)
    df["featured"] = df["station_id"].map(stations(league)["featured"]).fillna(False).astype(int)
    return df
