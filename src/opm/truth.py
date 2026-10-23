"""Training truth: PowderBench's own QC'd daily truth, as round windows.

- stations / observers / alps: Hugging Face `nakas/powderbench-truth`
  (station-local days, day_offset already applied, QC'd exactly as scored).
- era5: ERA5 (era5_seamless) daily snowfall from the Open-Meteo archive,
  pinned the same way PowderBench pins it.
- Recent resolved rounds of any league: data/rounds/<league>/<D>/truth.csv in
  the PowderBench checkout (used by the online bias layer).

Windows follow powderbench.truth.window_truth: horizon h = days D..D+h/24-1,
valid only when every day is valid.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from . import HORIZONS, WORK, pb_checkout

HF_REPO = "nakas/powderbench-truth"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def hf_daily(league: str, begin: date | None = None) -> pd.DataFrame:
    from huggingface_hub import hf_hub_download, list_repo_files

    files = sorted(f for f in list_repo_files(HF_REPO, repo_type="dataset")
                   if f.startswith(f"{league}/truth_") and f.endswith(".parquet"))
    # the latest export per calendar year wins (re-exports leave older chunks behind)
    by_year: dict[str, str] = {}
    for f in files:
        by_year[f.split("_")[1][:4]] = f
    frames = []
    for f in by_year.values():
        if begin and int(f.split("_")[2][:4]) < begin.year:
            continue
        p = hf_hub_download(HF_REPO, f, repo_type="dataset", cache_dir=str(WORK / "hf"))
        frames.append(pd.read_parquet(p))
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.rename(columns={"snow24_in": "snow24"})
    if begin:
        df = df[df["date"] >= begin]
    return df[["station_id", "date", "snow24", "valid"]].drop_duplicates(["station_id", "date"], keep="last")


def era5_daily(st: pd.DataFrame, begin: date, end: date, cache: Path | None = None) -> pd.DataFrame:
    from .sources.openmeteo import _get

    rows = []
    for i in range(0, len(st), 20):
        b = st.iloc[i : i + 20]
        params = {
            "latitude": ",".join(f"{x:.4f}" for x in b.latitude),
            "longitude": ",".join(f"{x:.4f}" for x in b.longitude),
            "elevation": ",".join(f"{x * 0.3048:.0f}" for x in b.elevation_ft),
            "daily": "snowfall_sum", "models": "era5_seamless", "timezone": "auto",
            "start_date": begin.isoformat(), "end_date": end.isoformat(),
        }
        for sid, r in zip(b.index, _get(ARCHIVE_URL, params, cache or WORK / "cache" / "era5", None, 0)):
            d = r.get("daily") or {}
            for t, v in zip(d.get("time", []), d.get("snowfall_sum", [])):
                rows.append({"station_id": sid, "date": date.fromisoformat(t),
                             "snow24": None if v is None else v / 2.54, "valid": v is not None})
    return pd.DataFrame(rows)


def windows(daily: pd.DataFrame) -> pd.DataFrame:
    """Daily truth -> (station_id, round_date, horizon_h, truth_in) for every
    round date where the window is fully valid."""
    d = daily[["station_id", "date", "snow24", "valid"]].copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d.set_index(["station_id", "date"]).sort_index()
    out = []
    for h in HORIZONS:
        n = h // 24
        parts = []
        for i in range(n):
            s = d.copy()
            s.index = s.index.set_levels(s.index.levels[1] - pd.Timedelta(days=i), level=1)
            parts.append(s)
        aligned = pd.concat([p["snow24"].where(p["valid"].astype(bool)) for p in parts], axis=1)
        tot = aligned.sum(axis=1, min_count=n)  # NaN unless every day is valid
        w = tot.dropna().rename("truth_in").reset_index().rename(columns={"date": "round_date"})
        w["horizon_h"] = h
        out.append(w)
    res = pd.concat(out, ignore_index=True)
    res["round_date"] = res["round_date"].dt.date
    return res


def resolved_rounds(league: str, since: date | None = None) -> pd.DataFrame:
    """Scored truth of resolved PowderBench rounds: station_id, round_date,
    horizon_h, truth_in."""
    frames = []
    for p in sorted((pb_checkout() / "data" / "rounds" / league).glob("*/truth.csv")):
        d = date.fromisoformat(p.parent.name)
        if since and d < since:
            continue
        t = pd.read_csv(p)
        t = t[t["valid"].astype(bool)]
        t["round_date"] = d
        frames.append(t[["station_id", "round_date", "horizon_h", "truth_in"]])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["station_id", "round_date", "horizon_h", "truth_in"])
