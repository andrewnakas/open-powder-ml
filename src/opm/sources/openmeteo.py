"""Open-Meteo: live multi-model forecasts and the lead-time-matched archive.

Live forecasts come from /v1/forecast. Training uses the Previous Runs API
(`<var>_previous_day<k>` = the value the run from k days earlier forecast for
that hour), so training features have the same lead time as a forecast made
at a PowderBench cutoff: local day D is ~1 day ahead, D+1 ~2 and D+2 ~3.

Everything is hourly in station-local time (timezone=auto) at the station's
own elevation (Open-Meteo's lapse-rate downscaling), so temperature-based
snow physics can be applied downstream (see features.py).

The free tier is ~10k weighted calls/day per IP (resets 00 UTC); a ledger
counts our weight so backfills stop cleanly before 429s.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from .. import WORK

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
PREVIOUS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"

# Global models available live and (precip + temperature) in the previous-runs archive.
GLOBAL_MODELS = (
    "ecmwf_ifs025",
    "gfs_seamless",
    "icon_seamless",
    "gem_seamless",
    "ukmo_seamless",
    "jma_seamless",
    "meteofrance_seamless",
)
CONUS_MODELS = ("ncep_nbm_conus", "ncep_hrrr_conus")
# Live-only extras: no archive to train on, used by the tier-0 consensus.
LIVE_ONLY = ("ecmwf_aifs025_single",)

VARS = ("precipitation", "temperature_2m", "snowfall")
# The archive has precip + temperature for nearly every model but snowfall for
# few; training uses our own snow physics, which also halves the quota cost.
ARCHIVE_VARS = ("precipitation", "temperature_2m")
BATCH = 20
UA = {"User-Agent": "open-powder-ml (+https://github.com/open-powder-ml)"}


class QuotaExhausted(RuntimeError):
    pass


def in_conus(lat: float, lon: float) -> bool:
    return 24.0 <= lat <= 50.0 and -125.5 <= lon <= -66.0


def models_for(lat: float, lon: float, live: bool = False) -> tuple[str, ...]:
    m = GLOBAL_MODELS + (CONUS_MODELS if in_conus(lat, lon) else ())
    return m + (LIVE_ONLY if live else ())


class Ledger:
    """Estimated weighted-call usage per UTC day (Open-Meteo counts a call
    per location, more for >10 variables or >14 days)."""

    def __init__(self, path: Path | None = None, budget: float | None = None, deadline: float | None = None):
        self.path = path or WORK / "openmeteo-ledger.json"
        self.budget = budget
        self.deadline = deadline  # time.time() after which requests stop (QuotaExhausted)
        self.note: str | None = None
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}

    def today(self) -> str:
        return pd.Timestamp.now(tz="UTC").date().isoformat()

    @property
    def used(self) -> float:
        return self.data.get(self.today(), 0.0)

    def charge(self, weight: float) -> None:
        if self.budget is not None and self.used + weight > self.budget:
            raise QuotaExhausted(f"budget {self.budget:.0f} reached ({self.used:.0f} used today)")
        self.data = {self.today(): self.used + weight}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data))


def call_weight(n_locations: int, n_vars: int, n_days: int) -> float:
    return n_locations * max(1.0, n_vars / 10) * max(1.0, n_days / 14)


def _get(url: str, params: dict, cache: Path | None, ledger: Ledger | None, weight: float) -> list[dict]:
    key = hashlib.sha1((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()
    cp = cache / f"{key}.json" if cache else None
    if cp and cp.exists():
        return json.loads(cp.read_text())
    def out_of_time() -> bool:
        return bool(ledger and ledger.deadline and time.time() > ledger.deadline)

    if out_of_time():
        raise QuotaExhausted("time budget for this run used up")
    if ledger:
        ledger.charge(weight)
    for attempt in range(6):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=180)
        except requests.RequestException:
            time.sleep(10 * (attempt + 1))
            continue
        if r.status_code == 429 or (r.status_code == 400 and "limit" in r.text.lower()):
            reason = r.text.lower()
            if "daily" in reason or "hourly" in reason:
                raise QuotaExhausted(r.text[:200])
            if out_of_time():
                raise QuotaExhausted("rate-limited and out of time")
            print(f"  open-meteo minutely limit, waiting ({attempt + 1}/6)", flush=True)
            time.sleep(61)  # minutely limit
            continue
        if r.status_code >= 500:
            time.sleep(15 * (attempt + 1))
            continue
        r.raise_for_status()
        out = r.json()
        out = out if isinstance(out, list) else [out]
        if cp:
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(out))
        return out
    raise RuntimeError(f"Open-Meteo kept failing: {url}")


def _frame(res: dict, station_id: str, models: tuple[str, ...], leads: tuple[int, ...],
           variables: tuple[str, ...] = VARS) -> pd.DataFrame:
    h = res.get("hourly") or {}
    t = pd.to_datetime(h.get("time", []))
    frames = []
    for m in models:
        for k in leads:
            suf = f"_previous_day{k}" if k else ""
            # a single-model request returns unsuffixed names
            cols = {v: h.get(f"{v}{suf}_{m}") if len(models) > 1 else h.get(f"{v}{suf}") for v in variables}
            if cols["precipitation"] is None or all(x is None for x in cols["precipitation"]):
                continue
            f = pd.DataFrame({"time": t, **{v: cols.get(v) or [None] * len(t) for v in VARS}})
            f["model"], f["lead"] = m, k
            frames.append(f)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["station_id"] = station_id
    return df


def fetch_hourly(
    st: pd.DataFrame,
    start: date,
    end: date,
    models: tuple[str, ...],
    leads: tuple[int, ...] = (0,),
    cache: Path | None = None,
    ledger: Ledger | None = None,
    sleep: float = 1.0,
    variables: tuple[str, ...] = VARS,
    batch: int = BATCH,
    partial_ok: bool = False,
) -> pd.DataFrame:
    """Hourly precip (mm), 2 m temperature (C) and snowfall (cm) per station,
    model and lead. st: index station_id, columns latitude/longitude/elevation_ft.
    leads=(0,) -> the live forecast; leads=(1,2,3) -> the Previous Runs archive."""
    previous = any(leads)
    url = PREVIOUS_URL if previous else FORECAST_URL
    hourly = [f"{v}_previous_day{k}" if k else v for v in variables for k in leads]
    n_days = (end - start).days + 1
    out = []
    for i in range(0, len(st), batch):
        b = st.iloc[i : i + batch]
        params = {
            "latitude": ",".join(f"{x:.4f}" for x in b.latitude),
            "longitude": ",".join(f"{x:.4f}" for x in b.longitude),
            "elevation": ",".join(f"{x * 0.3048:.0f}" for x in b.elevation_ft),
            "hourly": ",".join(hourly),
            "models": ",".join(models),
            "timezone": "auto",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        # each model counts like a separate location (observed: the daily
        # limit hits ~2x sooner than a per-location count predicts)
        w = call_weight(len(b) * len(models), len(hourly), n_days)
        try:
            res = _get(url, params, cache, ledger, w)
        except QuotaExhausted as e:
            if not out or not partial_ok:
                raise
            if ledger is not None:
                ledger.note = f"partial: {i}/{len(st)} stations ({e})"
            break
        for sid, r in zip(b.index, res):
            out.append(_frame(r, sid, models, leads, variables))
        if sleep:
            time.sleep(sleep)
    out = [f for f in out if len(f)]
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def fetch_by_region(st: pd.DataFrame, start: date, end: date, leads=(0,), live: bool = False, **kw) -> pd.DataFrame:
    """fetch_hourly with each station's applicable model set (CONUS models only
    inside CONUS)."""
    groups = st.groupby(st.apply(lambda r: models_for(r.latitude, r.longitude, live), axis=1))
    frames = [fetch_hourly(g, start, end, models, leads=leads, **kw) for models, g in groups]
    frames = [f for f in frames if len(f)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def season_chunks(begin: date, end: date, months=(10, 11, 12, 1, 2, 3, 4, 5)) -> list[tuple[date, date]]:
    """Whole calendar months inside the snow season that ended by `end`, so a
    unit never changes once harvested (and stays small and cacheable)."""
    spans = []
    for m in pd.period_range(begin, end, freq="M"):
        a, b = m.start_time.date(), m.end_time.date()
        if m.month in months and b <= end:
            spans.append((max(a, begin), b))
    return spans
