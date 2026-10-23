"""Hourly model output -> station-local daily aggregates -> round features.

Snow physics on top of every model's raw output: precipitation is split into
snow with a logistic rain/snow partition on the (elevation-downscaled) 2 m
temperature, and converted to depth with a temperature-dependent
snow-to-liquid ratio. Models' own `snowfall` (a fixed ~7-10:1 ratio, absent
for some models in the archive) is kept as a separate feature.

A round's horizon h covers local days D .. D + h/24 - 1, cumulative.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from . import HORIZONS

MM_PER_IN = 25.4
DAILY_COLS = ("prcp", "snow", "swe", "slr", "twet", "tmin")
WIN_VARS = ("prcp", "snow", "swe", "slr", "twet")


def snow_fraction(t_c: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp((t_c - 1.0) / 0.5))


def slr(t_c: np.ndarray) -> np.ndarray:
    """Snow-to-liquid ratio: ~10 at 0 C, ~14 at -5 C, capped at 20."""
    return np.clip(10.0 - 0.8 * t_c, 7.0, 20.0)


def daily(hourly: pd.DataFrame) -> pd.DataFrame:
    """Per (station_id, model, lead, local date). Hourly values are sums over
    the preceding hour, so the hour ending at 00:00 belongs to the day before."""
    if hourly.empty:
        return pd.DataFrame(columns=["station_id", "model", "lead", "date", *DAILY_COLS])
    h = hourly.copy()
    h["date"] = (pd.to_datetime(h["time"]) - pd.Timedelta(hours=1)).dt.date
    p = pd.to_numeric(h["precipitation"], errors="coerce")
    t = pd.to_numeric(h["temperature_2m"], errors="coerce")
    fs = snow_fraction(t.fillna(0.0).to_numpy())
    h["p"] = p
    h["sn"] = pd.to_numeric(h["snowfall"], errors="coerce")
    h["swe_h"] = p * fs
    h["slr_h"] = p * fs * slr(t.fillna(0.0).to_numpy())
    h["pt"] = p * t
    h["t"] = t
    g = h.groupby(["station_id", "model", "lead", "date"], sort=False)
    d = g.agg(
        prcp=("p", "sum"), n_p=("p", "count"), snow=("sn", lambda s: s.sum(min_count=1)),
        swe=("swe_h", "sum"), slr=("slr_h", "sum"), pt=("pt", "sum"), tmean=("t", "mean"), tmin=("t", "min"),
    ).reset_index()
    d = d[d["n_p"] >= 20]  # a nearly complete local day
    wet = d["prcp"] > 0.5
    d["twet"] = np.where(wet, d["pt"] / d["prcp"].where(wet), d["tmean"])
    d["prcp"] /= MM_PER_IN
    d["swe"] /= MM_PER_IN
    d["slr"] /= MM_PER_IN
    d["snow"] /= 2.54  # cm -> in
    return d[["station_id", "model", "lead", "date", *DAILY_COLS]]


def _windows(d: pd.DataFrame) -> pd.DataFrame:
    """d has station_id, model, round_date, i (day index 0..2) + daily vars ->
    cumulative windows per horizon, only where every day is present."""
    d = d[(d["i"] >= 0) & (d["i"] < 3)].sort_values("i")
    out = []
    keys = ["station_id", "model", "round_date"]
    for h in HORIZONS:
        n = h // 24
        w = d[d["i"] < n]
        g = w.groupby(keys, sort=False)
        agg = g.agg(
            n=("i", "nunique"), prcp=("prcp", "sum"), snow=("snow", lambda s: s.sum(min_count=len(s))),
            swe=("swe", "sum"), slr=("slr", "sum"), tmin=("tmin", "min"),
        )
        # precipitation-weighted temperature over the window
        tw = (w["twet"] * w["prcp"].clip(lower=0.02)).groupby([w[k] for k in keys]).sum()
        ws = w["prcp"].clip(lower=0.02).groupby([w[k] for k in keys]).sum()
        agg["twet"] = tw / ws
        agg = agg[agg["n"] == n].drop(columns="n").reset_index()
        agg["horizon_h"] = h
        out.append(agg)
    return pd.concat(out, ignore_index=True)


def live_windows(dly: pd.DataFrame, round_date: date) -> pd.DataFrame:
    d = dly[dly["lead"] == 0].copy()
    d["round_date"] = round_date
    d["i"] = (pd.to_datetime(d["date"]) - pd.Timestamp(round_date)).dt.days
    return _windows(d)


def archive_windows(dly: pd.DataFrame) -> pd.DataFrame:
    """Previous-runs daily rows (lead k = issued k days before) -> every round
    date: day i of round D uses lead i+1, like a forecast made at the cutoff."""
    d = dly[dly["lead"] >= 1].copy()
    d["i"] = d["lead"] - 1
    d["round_date"] = (pd.to_datetime(d["date"]) - pd.to_timedelta(d["i"], unit="D")).dt.date
    return _windows(d)


def wide(win: pd.DataFrame) -> pd.DataFrame:
    """One row per (station_id, round_date, horizon_h): per-model columns plus
    cross-model summaries."""
    keys = ["station_id", "round_date", "horizon_h"]
    p = win.pivot_table(index=keys, columns="model", values=list(WIN_VARS), aggfunc="first")
    p.columns = [f"{v}__{m}" for v, m in p.columns]
    slr_cols = [c for c in p.columns if c.startswith("slr__")]
    snow_cols = [c for c in p.columns if c.startswith("snow__")]
    prcp_cols = [c for c in p.columns if c.startswith("prcp__")]
    tw_cols = [c for c in p.columns if c.startswith("twet__")]
    swe_cols = [c for c in p.columns if c.startswith("swe__")]
    p["cons_slr"] = p[slr_cols].median(axis=1)
    p["mean_slr"] = p[slr_cols].mean(axis=1)
    p["max_slr"] = p[slr_cols].max(axis=1)
    p["min_slr"] = p[slr_cols].min(axis=1)
    p["sd_slr"] = p[slr_cols].std(axis=1)
    p["cons_snow"] = p[snow_cols].median(axis=1)
    p["cons_prcp"] = p[prcp_cols].median(axis=1)
    p["cons_swe"] = p[swe_cols].median(axis=1)
    p["cons_twet"] = p[tw_cols].median(axis=1)
    p["nmod"] = p[slr_cols].notna().sum(axis=1)
    p["frac_snowy"] = (p[slr_cols] >= 1.0).sum(axis=1) / p["nmod"].clip(lower=1)
    return p.reset_index()


def add_static(df: pd.DataFrame, st: pd.DataFrame, clim: pd.DataFrame, league: str) -> pd.DataFrame:
    """Station metadata, season and PowderBench climatology for each row."""
    from . import NH_LEAGUES

    df = df.copy()
    meta = st[["latitude", "longitude", "elevation_ft", "day_offset"]].rename(columns={"elevation_ft": "elev_ft"})
    df = df.join(meta, on="station_id")
    doy = pd.to_datetime(df["round_date"]).dt.dayofyear
    df["doy"] = doy.clip(upper=365)
    df["doy_sin"], df["doy_cos"] = np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)
    df["league_code"] = NH_LEAGUES.index(league) if league in NH_LEAGUES else len(NH_LEAGUES)
    if len(clim):
        c = clim.set_index(["station_id", "doy"])
        for h in HORIZONS:
            m = df["horizon_h"] == h
            idx = pd.MultiIndex.from_arrays([df.loc[m, "station_id"], df.loc[m, "doy"]])
            for src, dst in ((f"h{h}_p50", "climo_p50"), (f"h{h}_mean", "climo_mean")):
                if src in c.columns:
                    df.loc[m, dst] = c[src].reindex(idx).to_numpy()
        if "h24_p6freq" in c.columns:
            idx = pd.MultiIndex.from_arrays([df["station_id"], df["doy"]])
            df["climo_p6"] = c["h24_p6freq"].reindex(idx).to_numpy()
    for col in ("climo_p50", "climo_mean", "climo_p6"):
        if col not in df.columns:
            df[col] = np.nan
    return df.drop(columns="doy")
