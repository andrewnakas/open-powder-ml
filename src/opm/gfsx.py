"""When the snow falls inside the window: hourly-GFS timing features.

An observer reads a board at a fixed hour, so snow just before the window
opens or just after it closes often lands in the neighbouring day's report,
and the daily totals the multi-model block sees cannot tell those cases
apart. One hourly GFS run (dynamical.org archive, no quota) can.

Ported from deep-powder (src/dpow/nbhd.py there), where these six columns
added 1.5 CV points; here the season CV went 44.30 -> 45.85 (24 h 33.6 ->
37.6, every fold and league up; experiment variant `gfsx`, 2026-11-17).

- Training: the harvested features come from deep-powder's public `nbhd`
  release (files gfsx2__<league>__<season>.parquet; its daily pipeline keeps
  the current season current). Rows it does not cover stay NaN.
- Live: computed here from the newest GFS run a forecast at the cutoff can
  use (the same rule deep-powder's harvest used: published 9 h before the
  cutoff, else the newest run up to a day older).

Columns (inches of snow; the window is local days D..D+h/24-1, neighbourhood
mean of the 3x3 cells around the station):
  gfsx_first12, gfsx_last12   first and last 12 local hours of the window
  gfsx_pre12, gfsx_post6      just before / just after it
  gfsx_peak6, gfsx_hours      heaviest 6 h; hours with snow
"""

from __future__ import annotations

import time
from datetime import date
from functools import cache

import numpy as np
import pandas as pd

from . import HORIZONS

COLS = ("gfsx_first12", "gfsx_last12", "gfsx_pre12", "gfsx_post6", "gfsx_peak6", "gfsx_hours")
LEAGUES = ("stations", "observers", "alps")  # what deep-powder harvests for training
SOURCE_REPO = "andrewnakas/deep-powder"
ICECHUNK = ("dynamical-noaa-gfs", "noaa-gfs-forecast/v0.2.7.icechunk")
N_LEAD = 105  # hourly steps read: covers the 72 h window from any usable run
LAG_H = 9.0  # run published (7 h) plus a 2 h margin before the cutoff
MM_PER_IN = 25.4
OFFSETS = [(i, j) for i in (-1, 0, 1) for j in (-1, 0, 1)]

_ds = None


def _open():
    global _ds
    if _ds is None:
        import icechunk
        import xarray as xr

        st = icechunk.s3_storage(bucket=ICECHUNK[0], prefix=ICECHUNK[1], region="us-west-2", anonymous=True)
        store = icechunk.Repository.open(st).readonly_session("main").store
        _ds = xr.open_zarr(store, consolidated=False, decode_timedelta=True, chunks=None)
    return _ds


@cache
def _tz(lat: float, lon: float) -> str:
    from timezonefinder import TimezoneFinder

    return TimezoneFinder().timezone_at(lat=lat, lng=lon) or "UTC"


def _read(init: pd.Timestamp, st: pd.DataFrame, retries: int = 5):
    """-> neighbourhood-mean hourly snow (in), (station, hour), and the hours' UTC midpoints."""
    import xarray as xr

    ds = _open()
    glat, glon = ds["latitude"].values, ds["longitude"].values
    lon = ((st["longitude"].to_numpy() + 180) % 360) - 180
    li = np.abs(glat[:, None] - st["latitude"].to_numpy()[None]).argmin(0)
    lo = np.abs(glon[:, None] - lon[None]).argmin(0)
    cells = np.stack([[np.clip(li + i, 0, len(glat) - 1), (lo + j) % len(glon)] for i, j in OFFSETS], 1)
    flat = cells[0] * len(glon) + cells[1]
    uniq, inv = np.unique(flat, return_inverse=True)
    inv = inv.reshape(flat.shape).T  # (n, 9)
    sub = (ds[["precipitation_surface", "temperature_2m", "percent_frozen_precipitation_surface"]]
           .sel(init_time=np.datetime64(init)).isel(lead_time=slice(0, N_LEAD))
           .isel(latitude=xr.DataArray(uniq // len(glon), dims="pt"),
                 longitude=xr.DataArray(uniq % len(glon), dims="pt")))
    for attempt in range(retries):
        try:
            v = sub.load()
            break
        except Exception:  # flaky range reads
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))
    lead_h = np.asarray(pd.to_timedelta(v["lead_time"].values) / pd.Timedelta(hours=1), float)
    dt = np.diff(lead_h, prepend=0.0)
    keep = dt > 0
    arr = lambda n: v[n].transpose("pt", "lead_time").to_numpy().astype("float32")[:, keep]  # noqa: E731
    liq = np.nan_to_num(arr("precipitation_surface")).clip(min=0) * (dt[keep] * 3600).astype("float32")  # mm
    t = arr("temperature_2m")
    fz = arr("percent_frozen_precipitation_surface") / 100.0
    fz = np.clip(np.nan_to_num(np.where(np.isfinite(fz), fz, 1.0 / (1.0 + np.exp((t - 1.0) / 0.5)))), 0, 1)
    snow = liq * fz * np.clip(10.0 - 0.8 * np.nan_to_num(t), 7.0, 20.0) / MM_PER_IN
    mid = (pd.Timestamp(init) + pd.to_timedelta(lead_h[keep] - dt[keep] / 2, unit="h")).to_numpy()
    return snow[inv].mean(1), mid


def _local_hours(mid: np.ndarray, st: pd.DataFrame, round_date: date) -> np.ndarray:
    """Hours from local midnight of the round date to each step's midpoint, (station, hour)."""
    tz = pd.Series([_tz(round(a, 3), round(b, 3)) for a, b in zip(st.latitude, st.longitude)], index=st.index)
    out = np.empty((len(st), len(mid)), "float32")
    utc = pd.DatetimeIndex(mid).tz_localize("UTC")
    d0 = pd.Timestamp(round_date)
    for zone, idx in tz.groupby(tz).groups.items():
        rel = (utc.tz_convert(zone).tz_localize(None) - d0) / pd.Timedelta(hours=1)
        out[st.index.get_indexer(idx)] = np.asarray(rel, "float32")
    return out


def features(init: pd.Timestamp, st: pd.DataFrame, round_date: date) -> pd.DataFrame:
    """One run -> station_id, horizon_h + COLS (identical to deep-powder's gfsx2 columns)."""
    s, mid = _read(init, st)
    r = _local_hours(mid, st, round_date)
    rows = []
    for h in HORIZONS:
        w = (r >= 0) & (r < h)
        full = w.sum(1) >= h - 1  # the run covers the whole window
        six = np.stack([(s * (w & (r >= a) & (r < a + 6))).sum(1) for a in range(0, h - 5, 3)], 1)
        f = pd.DataFrame({
            "gfsx_first12": (s * (w & (r < 12))).sum(1),
            "gfsx_last12": (s * (w & (r >= h - 12))).sum(1),
            "gfsx_pre12": (s * ((r >= -12) & (r < 0))).sum(1),
            "gfsx_post6": (s * ((r >= h) & (r < h + 6))).sum(1),
            "gfsx_peak6": six.max(1),
            "gfsx_hours": ((s > 0.01) & w).sum(1),
        }, index=st.index).astype("float32")
        f[~full] = np.nan
        f["horizon_h"] = h
        rows.append(f.rename_axis("station_id").reset_index())
    return pd.concat(rows, ignore_index=True).dropna(subset=["gfsx_first12"])


def for_round(cutoff, st: pd.DataFrame, round_date: date) -> pd.DataFrame:
    """Live: the features from the newest run a forecast at `cutoff` (UTC) may use."""
    want = pd.Timestamp(cutoff).tz_localize(None) if pd.Timestamp(cutoff).tzinfo is None else \
        pd.Timestamp(cutoff).tz_convert("UTC").tz_localize(None)
    want = want - pd.Timedelta(hours=LAG_H)
    have = pd.DatetimeIndex(_open().init_time.values)
    ok = have[(have <= want) & (have >= want - pd.Timedelta(hours=30))]
    if not len(ok):
        return pd.DataFrame()
    return features(ok.max(), st, round_date)


def join(df: pd.DataFrame) -> pd.DataFrame:
    """Training: left-join deep-powder's harvested features onto a table (adds COLS)."""
    import subprocess
    import tempfile
    from pathlib import Path

    df = df.drop(columns=[c for c in COLS if c in df])
    dest = Path(tempfile.mkdtemp())
    try:
        subprocess.run(["gh", "release", "download", "nbhd", "-R", SOURCE_REPO, "-p", "gfsx2__*.parquet",
                        "-D", str(dest)], check=True)
    except Exception as e:  # never block training on it: the columns are simply missing
        print(f"  gfsx: download failed ({e}); training without", flush=True)
        return df
    parts = []
    for f in sorted(dest.glob("gfsx2__*.parquet")):
        g = pd.read_parquet(f, columns=["station_id", "round_date", "horizon_h", *COLS])
        g["league"] = f.stem.split("__")[1]
        parts.append(g)
    if not parts:
        return df
    g = pd.concat(parts, ignore_index=True)
    g["round_date"] = pd.to_datetime(g["round_date"]).dt.date
    g = g.drop_duplicates(["league", "station_id", "round_date", "horizon_h"], keep="last")
    key = pd.to_datetime(df["round_date"]).dt.date
    out = df.assign(_rd=key).merge(g.rename(columns={"round_date": "_rd"}),
                                   on=["league", "station_id", "_rd", "horizon_h"], how="left").drop(columns="_rd")
    for c in COLS:
        out[c] = out[c].astype("float32")
    print(f"  gfsx: {out['gfsx_first12'].notna().mean():.0%} of rows have the timing features", flush=True)
    return out
