"""Snowpack state before the cutoff (stations league): what the sensor
already holds, so the model can learn how new snow settles and blows on top.

SNOTEL truth is the *change* in snow depth. How much a new fall shows up in
that change depends on the pack underneath: a dense, deep, warm pack settles
fast and compacts new snow; bare ground melts the first inches. These are
measured, public, real-time readings (NRCS AWDB, via PowderBench's own
fetcher), so they are fair game as long as they predate the cutoff.

Timing: AWDB daily values are end-of-day readings (periodRef END). For round
D (cutoff 00Z D, the afternoon of D-1 in North America) the newest reading
taken before the cutoff is the end of local day D-2 (midnight starting D-1).
Features use only readings dated <= D-2.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

LEAGUES = ("stations",)
LAG_DAYS = 2  # newest usable daily reading: D-2
ELEMENTS = ("SNWD", "WTEQ", "TAVG")
COLS = ("sp_depth", "sp_swe", "sp_density", "sp_new1", "sp_new3", "sp_depth_chg3", "sp_tavg", "sp_bare")
AWDB_SUFFIXES = (":SNTL", ":SNTLT", ":MSNT")  # networks AWDB serves (not CDEC)


TAG = "snowpack"  # release holding the daily observation archive (one file per water year)
_PULLED = False


def archive_dir():
    from . import WORK

    return WORK / "snowpack"


def load_archive(station_ids, begin, end) -> pd.DataFrame:
    """Archived daily readings (pulled from the `snowpack` release once per
    process): NRCS AWDB throttles bursts from shared CI runners, so training
    reads history from here and only the last days come live from AWDB."""
    from . import store

    global _PULLED
    d = archive_dir()
    if not _PULLED:  # fetch any water years not already here (keep fresher local files)
        store.pull(TAG, d, "obs_*.parquet", overwrite=False)
        _PULLED = True
    files = sorted(d.glob("obs_*.parquet"))
    if not files:
        return pd.DataFrame(columns=["station_id", "date", *ELEMENTS])
    o = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    o["date"] = pd.to_datetime(o["date"]).dt.date
    ids = set(station_ids)
    return o[o["station_id"].isin(ids) & (o["date"] >= begin) & (o["date"] <= end)]


def fetch(station_ids: list[str], begin, end) -> pd.DataFrame:
    from powderbench.snotel import fetch_daily

    ids = [s for s in station_ids if s.endswith(AWDB_SUFFIXES)]
    if not ids:
        return pd.DataFrame(columns=["station_id", "date", *ELEMENTS])
    return fetch_daily(ids, begin, end, elements=ELEMENTS)


def features(keys: pd.DataFrame, obs: pd.DataFrame | None = None) -> pd.DataFrame:
    """keys: station_id, round_date (any number of rows) -> one row per key
    with the sp_* columns (NaN where the station has no AWDB record)."""
    k = keys[["station_id", "round_date"]].drop_duplicates()
    if k.empty:
        return pd.DataFrame(columns=["station_id", "round_date", *COLS])
    rd = pd.to_datetime(k["round_date"])
    if obs is None:
        ids = sorted(k["station_id"].unique())
        begin = (rd.min() - timedelta(days=LAG_DAYS + 6)).date()
        end = (rd.max() - timedelta(days=LAG_DAYS)).date()
        obs = load_archive(ids, begin, end)
        have_to = max(obs["date"]) if len(obs) else None
        if have_to is None or have_to < end:  # the recent tail (live forecasts): straight from AWDB
            gap_from = max(begin, (have_to + timedelta(days=1)) if have_to else begin)
            if (end - gap_from).days <= 45:
                try:
                    obs = pd.concat([obs, fetch(ids, gap_from, end)], ignore_index=True)
                except Exception as e:
                    print(f"snowpack: live AWDB fetch failed ({e}); using the archive only", flush=True)
    if obs.empty:
        return k.assign(**{c: np.nan for c in COLS})
    o = obs.copy()
    o["date"] = pd.to_datetime(o["date"])
    o = o.set_index(["station_id", "date"]).sort_index()
    for c in ELEMENTS:
        o[c] = pd.to_numeric(o[c], errors="coerce")
    o.loc[(o["SNWD"] < 0) | (o["SNWD"] > 400), "SNWD"] = np.nan

    def at(lag: int, col: str) -> np.ndarray:
        idx = pd.MultiIndex.from_arrays([k["station_id"], rd - pd.Timedelta(days=LAG_DAYS + lag)])
        return o[col].reindex(idx).to_numpy()

    d0, d1, d3 = at(0, "SNWD"), at(1, "SNWD"), at(3, "SNWD")
    swe = at(0, "WTEQ")
    out = k.copy()
    out["sp_depth"] = d0
    out["sp_swe"] = swe
    out["sp_density"] = np.where(d0 > 2, swe / np.where(d0 > 2, d0, np.nan), np.nan)
    out["sp_new1"] = np.clip(d0 - d1, 0, None)
    new = [np.clip(at(i, "SNWD") - at(i + 1, "SNWD"), 0, None) for i in range(3)]
    out["sp_new3"] = np.nansum(np.vstack(new), axis=0) if len(new) else np.nan
    out.loc[np.isnan(np.vstack(new)).all(axis=0), "sp_new3"] = np.nan
    out["sp_depth_chg3"] = d0 - d3
    out["sp_tavg"] = at(0, "TAVG")
    out["sp_bare"] = np.where(np.isnan(d0), np.nan, (d0 < 1).astype(float))
    return out


def add(df: pd.DataFrame, league: str) -> pd.DataFrame:
    """Join snowpack features onto rows (station_id, round_date, ...) of a
    league that measures snow depth with sensors; other leagues unchanged."""
    if league not in LEAGUES or df.empty:
        return df
    try:
        f = features(df[["station_id", "round_date"]])
    except Exception as e:  # data unavailable: forecast without them (NaN, as for CDEC stations)
        print(f"snowpack features unavailable: {e}", flush=True)
        return df
    f["round_date"] = pd.to_datetime(f["round_date"]).dt.date
    out = df.copy()
    out["round_date"] = pd.to_datetime(out["round_date"]).dt.date
    return out.merge(f, on=["station_id", "round_date"], how="left")
