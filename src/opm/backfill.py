"""Quota-aware, resumable harvest of lead-matched archive forecasts.

A unit = (league, ~2-month span, batch of <=20 stations sharing a model set).
Finished units are parquet files in work/store (synced to the `store`
release), so each daily run continues where the last stopped and stops
cleanly when the Open-Meteo budget for the day is used.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from . import WORK, features, store
from .contract import stations
from .sources import openmeteo as om

# The Previous Runs archive is dense for most models from late 2024.
ARCHIVE_BEGIN = date(2024, 10, 1)
# Only whole months harvest; give the archive a few days to settle.
LAG_DAYS = 3


def training_stations(league: str, network_sample: int = 0, seed: int = 7) -> pd.DataFrame:
    st = stations(league)
    feat = st[st["featured"]]
    if network_sample:
        rest = st[~st["featured"]]
        feat = pd.concat([feat, rest.sample(min(network_sample, len(rest)), random_state=seed)])
    return feat.sort_index()


def units(league: str, begin: date, end: date, network_sample: int = 0) -> list[tuple]:
    st = training_stations(league, network_sample)
    months = (5, 6, 7, 8, 9, 10) if league in ("era5", "resorts") else (10, 11, 12, 1, 2, 3, 4, 5)
    if league in ("resorts", "resorts-na"):  # truth exists only since PowderBench rounds began
        begin = max(begin, date(2026, 6, 1))
    keyed = st.assign(_m=st.apply(lambda r: om.models_for(r.latitude, r.longitude), axis=1))
    out = []
    for a, b in om.season_chunks(begin, end, months):
        for models, g in keyed.groupby("_m"):
            for i in range(0, len(g), om.BATCH):
                part = g.iloc[i : i + om.BATCH].drop(columns="_m")
                out.append((store.unit_name(league, a, b, list(part.index)), a, b, part, models))
    return out


# Snowiest months first, newest season first within each: the first days of
# harvest already give a model trained on what matters.
MONTH_RANK = {m: i for i, m in enumerate((1, 2, 12, 3, 11, 4, 10, 5, 7, 8, 6, 9))}


def _priority(u) -> tuple:
    a: date = u[1]
    sh = u[0].split("__")[1] in ("era5", "resorts")
    rank = MONTH_RANK[(a.month + 6 - 1) % 12 + 1] if sh else MONTH_RANK[a.month]
    season = a.year if sh else (a.year if a.month >= 8 else a.year - 1)
    return (rank // 4, -season, rank)


def run(leagues: list[str], budget: float, network_sample: int = 0, end: date | None = None) -> dict:
    end = end or (pd.Timestamp.utcnow() - pd.Timedelta(days=LAG_DAYS)).date()
    have = {p.name for p in store.STORE.glob("*.parquet")} | store.remote_assets("store")
    ledger = om.Ledger(budget=budget)
    done, new_files = 0, []
    todo = [u for lg in leagues for u in units(lg, ARCHIVE_BEGIN, end, network_sample) if u[0] not in have]
    todo.sort(key=_priority)
    print(f"backfill: {len(todo)} units to go, budget {budget:.0f} (used today {ledger.used:.0f})")
    try:
        for name, a, b, part, models in todo:
            h = om.fetch_hourly(part, a, b, models, leads=(1, 2, 3), ledger=ledger,
                                variables=om.ARCHIVE_VARS, cache=WORK / "cache" / "previous-runs")
            d = features.daily(h)
            path = store.STORE / name
            path.parent.mkdir(parents=True, exist_ok=True)
            d.to_parquet(path, index=False)
            new_files.append(path)
            done += 1
            print(f"  {name}: {len(d)} rows")
    except om.QuotaExhausted as e:
        print(f"backfill: stopping for today — {e}")
    store.push("store", new_files)
    return {"done": done, "remaining": len(todo) - done, "used": ledger.used}
