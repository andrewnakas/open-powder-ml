"""Terrain features for every PowderBench station from the Copernicus GLO-30 DEM.

Method from mountainweather/mtnwx (MIT, same author): windowed reads of the
public GLO-30 Cloud-Optimized GeoTIFFs on AWS Open Data (no credentials), per
station:

- dem_elevation_m, local_relief_m (max - min within 10 km)
- slope_deg, aspect_deg (3x3 gradient at the station cell)
- tpi_500m / tpi_2km / tpi_10km (station minus the mean of the window)
- mean_elev_2km / mean_elev_14km: the terrain a ~3 km (HRRR) and a 0.25 deg
  (GEFS/ECMWF/GFS) grid cell "sees"; elev_delta_* = station minus that mean.
  Coarse models' 2 m temperature sits at their smoothed orography, so this
  delta drives most of their warm bias on mountain stations.

Output: data/terrain.parquet (committed; terrain doesn't change). Run:
    python scripts/terrain.py            # all leagues' stations
DEM: Copernicus DEM GLO-30, (c) DLR e.V. 2010-2014 and (c) Airbus Defence and
Space GmbH 2014-2018, provided under COPERNICUS by the European Union and ESA.
"""

from __future__ import annotations

import math
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

URL = ("https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM/"
       "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM.tif")
M_PER_DEG = 111_320.0
LEAGUES = ("stations", "observers", "alps", "resorts-na", "era5", "resorts")


def tile_url(lat: float, lon: float) -> str:
    la, lo = math.floor(lat), math.floor(lon)
    return URL.format(ns="N" if la >= 0 else "S", lat=abs(la), ew="E" if lo >= 0 else "W", lon=abs(lo))


def _window(src, lat, lon, radius_m, decimate=1):
    from rasterio.windows import Window

    row, col = src.index(lon, lat)
    cell_m = abs(src.transform.a) * M_PER_DEG * math.cos(math.radians(lat))
    half = max(1, int(round(radius_m / cell_m)))
    win = Window(col - half, row - half, 2 * half + 1, 2 * half + 1)
    n = max(3, (2 * half + 1) // decimate) | 1
    data = src.read(1, window=win, boundless=True, fill_value=np.nan, out_shape=(n, n)).astype("float64")
    data[data < -1000] = np.nan
    return data, cell_m * (2 * half + 1) / n


def _centre(p, half):
    c = p.shape[0] // 2
    return p[c - half: c + half + 1, c - half: c + half + 1] if half >= 1 else p


def station_terrain(src, sid, lat, lon, elev_m) -> dict:
    near, cell = _window(src, lat, lon, 10_000)  # full 30 m resolution
    c = near.shape[0] // 2
    z = near[c - 1: c + 2, c - 1: c + 2]
    dzdx = ((z[0, 2] + 2 * z[1, 2] + z[2, 2]) - (z[0, 0] + 2 * z[1, 0] + z[2, 0])) / (8 * cell)
    dzdy = ((z[2, 0] + 2 * z[2, 1] + z[2, 2]) - (z[0, 0] + 2 * z[0, 1] + z[0, 2])) / (8 * cell)
    centre = near[c, c]
    far, _ = _window(src, lat, lon, 14_000, decimate=8)  # a 0.25 deg cell, at ~240 m
    m2 = float(np.nanmean(_centre(near, int(1500 / cell))))
    m14 = float(np.nanmean(far))
    return {
        "station_id": sid, "dem_elevation_m": float(centre),
        "local_relief_m": float(np.nanmax(near) - np.nanmin(near)),
        "slope_deg": math.degrees(math.atan(math.hypot(dzdx, dzdy))),
        "aspect_deg": (math.degrees(math.atan2(dzdy, -dzdx)) + 360.0) % 360.0,
        "tpi_500m": float(centre - np.nanmean(_centre(near, int(500 / cell)))),
        "tpi_2km": float(centre - np.nanmean(_centre(near, int(2000 / cell)))),
        "tpi_10km": float(centre - np.nanmean(near)),
        "mean_elev_2km": m2, "mean_elev_14km": m14,
        "elev_delta_2km": elev_m - m2, "elev_delta_14km": elev_m - m14,
    }


def run(out: Path) -> pd.DataFrame:
    import rasterio
    from rasterio.env import Env

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from opm.contract import stations

    st = pd.concat([stations(lg)[["latitude", "longitude", "elevation_ft"]] for lg in LEAGUES])
    st = st[~st.index.duplicated()]
    done = pd.read_parquet(out) if out.exists() else pd.DataFrame(columns=["station_id"])
    st = st[~st.index.isin(done["station_id"])]
    st["tile"] = [tile_url(a, b) for a, b in zip(st.latitude, st.longitude)]
    print(f"{len(st)} stations over {st.tile.nunique()} DEM tiles", flush=True)

    def one_tile(item):
        url, g = item
        rows = []
        with Env(AWS_NO_SIGN_REQUEST="YES", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
            try:
                src = rasterio.open(url)
            except Exception as e:  # ocean / unreleased tile
                print(f"  skip {url.split('/')[-1]}: {e}", flush=True)
                return rows
            with src:
                for sid, s in g.iterrows():
                    try:
                        rows.append(station_terrain(src, sid, s.latitude, s.longitude, s.elevation_ft * 0.3048))
                    except Exception as e:
                        print(f"  {sid}: {e}", flush=True)
        return rows

    rows = []
    with ThreadPoolExecutor(8) as ex:
        for i, r in enumerate(ex.map(one_tile, st.groupby("tile"))):
            rows += r
            if i % 25 == 0:
                print(f"  {i} tiles, {len(rows)} stations", flush=True)
    res = pd.concat([done, pd.DataFrame(rows)], ignore_index=True)
    res.to_parquet(out, index=False)
    return res


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "data" / "terrain.parquet"
    out.parent.mkdir(exist_ok=True)
    r = run(out)
    print(f"wrote {out}: {len(r)} stations")
    print(r.describe().T[["mean", "min", "max"]].round(1).to_string())
