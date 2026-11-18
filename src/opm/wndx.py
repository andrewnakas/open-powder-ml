"""Storm wind, moisture and 80 m temperature during the snow (hourly GFS).

Ported from deep-powder (src/dpow/wndx.py there), where these eight columns
added 0.52 season-CV points with every fold up (2026-11-17; the moisture and
temperature columns carry the gain, wind alone was +0.10).

- Training: deep-powder's public `nbhd` release (files wndx__<league>__<season>.parquet).
- Live: computed here from the same GFS run gfsx.for_round picks.

Columns, each weighted by the station cell's hourly precipitation inside the
window (plain window mean when dry):
  wndx_u, wndx_v, wndx_spd   100 m wind (m/s)
  wndx_up                    flow up the grid's terrain slope (mm/s)
  wndx_up_asp                flow onto the station's local slope face (DEM aspect and slope)
  wndx_pw, wndx_rh, wndx_t80 precipitable water, 2 m relative humidity, 80 m temperature
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from . import HORIZONS
from .gfsx import LAG_H, N_LEAD, OFFSETS, SOURCE_REPO, _local_hours, _open

COLS = ("wndx_u", "wndx_v", "wndx_spd", "wndx_up", "wndx_up_asp", "wndx_pw", "wndx_rh", "wndx_t80")
VARS = ("precipitation_surface", "wind_u_100m", "wind_v_100m", "pressure_surface",
        "precipitable_water_atmosphere", "relative_humidity_2m", "temperature_80m")
SCALE_H = 8000.0


def _read(init: pd.Timestamp, st: pd.DataFrame, retries: int = 5):
    import xarray as xr

    ds = _open()
    glat, glon = ds["latitude"].values, ds["longitude"].values
    lat = st["latitude"].to_numpy()
    lon = ((st["longitude"].to_numpy() + 180) % 360) - 180
    li = np.abs(glat[:, None] - lat[None]).argmin(0)
    lo = np.abs(glon[:, None] - lon[None]).argmin(0)
    cells = np.stack([[np.clip(li + i, 1, len(glat) - 2) if i else li, (lo + j) % len(glon)] for i, j in OFFSETS], 1)
    flat = cells[0] * len(glon) + cells[1]
    uniq, inv = np.unique(flat, return_inverse=True)
    inv = inv.reshape(flat.shape).T  # (n, 9)
    sub = (ds[list(VARS)].sel(init_time=np.datetime64(init)).isel(lead_time=slice(0, N_LEAD))
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

    def arr(n):
        return v[n].transpose("pt", "lead_time").to_numpy().astype("float32")[:, keep]

    own = {n: arr(n)[inv[:, 4]] for n in VARS if n != "pressure_surface"}
    own["precipitation_surface"] = np.nan_to_num(own["precipitation_surface"]).clip(min=0) * dt[keep]
    z = -SCALE_H * np.log(np.nanmean(arr("pressure_surface"), 1).clip(min=1.0))[inv]  # (n, 9)
    up_i, dn_i, lf_j, rt_j = 1, 7, 3, 5  # OFFSETS (-1,0), (1,0), (0,-1), (0,1)
    dy = (glat[np.clip(li - 1, 0, len(glat) - 1)] - glat[np.clip(li + 1, 0, len(glat) - 1)]) * 111_000.0
    dx = 2 * (glon[1] - glon[0]) * 111_000.0 * np.cos(np.radians(lat))
    dzdy = (z[:, up_i] - z[:, dn_i]) / np.where(dy == 0, np.nan, dy)
    dzdx = (z[:, rt_j] - z[:, lf_j]) / dx
    mid = (pd.Timestamp(init) + pd.to_timedelta(lead_h[keep] - dt[keep] / 2, unit="h")).to_numpy()
    return own, dzdx.astype("float32"), dzdy.astype("float32"), dt[keep], mid


def _terrain(st: pd.DataFrame):
    p = Path(__file__).resolve().parents[2] / "data" / "terrain.parquet"
    if not p.exists():
        nan = np.full(len(st), np.nan)
        return nan, nan, nan
    t = pd.read_parquet(p, columns=["station_id", "aspect_deg", "slope_deg"]).set_index("station_id")
    t = t[~t.index.duplicated()].reindex(st.index)
    a, s = np.radians(t["aspect_deg"].to_numpy(float)), np.radians(t["slope_deg"].to_numpy(float))
    return np.sin(a), np.cos(a), np.tan(s)


def features(init: pd.Timestamp, st: pd.DataFrame, round_date: date) -> pd.DataFrame:
    """One run -> station_id, horizon_h + COLS (identical to deep-powder's wndx columns)."""
    own, dzdx, dzdy, dt, mid = _read(init, st)
    r = _local_hours(mid, st, round_date)
    asin, acos, tan = _terrain(st)
    u, v = own["wind_u_100m"], own["wind_v_100m"]
    fields = {"u": u, "v": v, "spd": np.hypot(u, v),
              "up": (u * dzdx[:, None] + v * dzdy[:, None]) * 1000.0,
              "up_asp": -(u * asin[:, None] + v * acos[:, None]) * tan[:, None],
              "pw": own["precipitable_water_atmosphere"], "rh": own["relative_humidity_2m"],
              "t80": own["temperature_80m"]}
    p = own["precipitation_surface"]
    rows = []
    for h in HORIZONS:
        w = (r >= 0) & (r < h)
        full = (w * dt).sum(1) >= h - 1
        wt = p * w
        tot = wt.sum(1, keepdims=True)
        wt = np.where(tot > 0.1, wt / np.where(tot > 0, tot, 1), w / np.clip(w.sum(1, keepdims=True), 1, None))
        f = pd.DataFrame({f"wndx_{k}": np.nansum(np.nan_to_num(a) * wt, 1) for k, a in fields.items()},
                         index=st.index).astype("float32")
        f[~full] = np.nan
        f["horizon_h"] = h
        rows.append(f.rename_axis("station_id").reset_index())
    return pd.concat(rows, ignore_index=True).dropna(subset=["wndx_u"])


def for_round(cutoff, st: pd.DataFrame, round_date: date) -> pd.DataFrame:
    """Live: from the newest GFS run a forecast at `cutoff` may use (same rule as gfsx.for_round)."""
    c = pd.Timestamp(cutoff)
    want = (c.tz_convert("UTC").tz_localize(None) if c.tzinfo else c) - pd.Timedelta(hours=LAG_H)
    have = pd.DatetimeIndex(_open().init_time.values)
    ok = have[(have <= want) & (have >= want - pd.Timedelta(hours=30))]
    if not len(ok):
        return pd.DataFrame()
    return features(ok.max(), st, round_date)


def join(df: pd.DataFrame) -> pd.DataFrame:
    """Training: left-join deep-powder's harvested features onto a table (adds COLS)."""
    import subprocess
    import tempfile

    df = df.drop(columns=[c for c in COLS if c in df])
    dest = Path(tempfile.mkdtemp())
    try:
        subprocess.run(["gh", "release", "download", "nbhd", "-R", SOURCE_REPO, "-p", "wndx__*.parquet",
                        "-D", str(dest)], check=True)
    except Exception as e:  # never block training on it
        print(f"  wndx: download failed ({e}); training without", flush=True)
        return df
    parts = []
    for f in sorted(dest.glob("wndx__*.parquet")):
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
    print(f"  wndx: {out['wndx_u'].notna().mean():.0%} of rows have the storm-wind features", flush=True)
    return out
