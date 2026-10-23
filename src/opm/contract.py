"""The public PowderBench contract: open rounds, stations, cutoffs.

Rounds are read from `data/rounds/<league>/<D>/round.json` in our fresh
PowderBench checkout (scripts/checkout_powderbench.sh) — the same manifests
the auto-merge bot checks. powderbench.com/api/v1/open-rounds.json is only
rebuilt once a day, so it is not relied on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from functools import cache

import pandas as pd

from . import pb_checkout, use_pb_data

use_pb_data()


@dataclass(frozen=True)
class Round:
    league: str
    round_date: date
    cutoff: datetime
    target_days: tuple[date, ...]
    stations: tuple[str, ...]
    featured: tuple[str, ...] = field(default=())

    @property
    def key(self) -> str:
        return f"{self.league}/{self.round_date.isoformat()}"

    def hours_left(self, now: datetime) -> float:
        return (self.cutoff - now).total_seconds() / 3600


def _parse(m: dict) -> Round:
    return Round(
        league=m["league"],
        round_date=date.fromisoformat(m["round_id"]),
        cutoff=datetime.fromisoformat(m["cutoff_utc"]).astimezone(timezone.utc),
        target_days=tuple(date.fromisoformat(d) for d in m["target_days"]),
        stations=tuple(m["stations"]),
        featured=tuple(m.get("featured") or m["stations"]),
    )


def open_rounds(now: datetime | None = None, leagues: list[str] | None = None) -> list[Round]:
    """Every round whose manifest says open and whose cutoff is still ahead."""
    now = now or datetime.now(timezone.utc)
    out = []
    for p in sorted((pb_checkout() / "data" / "rounds").glob("*/*/round.json")):
        m = json.loads(p.read_text())
        if m.get("status") != "open" or (leagues and m["league"] not in leagues):
            continue
        r = _parse(m)
        if r.cutoff > now:
            out.append(r)
    return sorted(out, key=lambda r: r.cutoff)


def load_round(league: str, d: date) -> Round:
    p = pb_checkout() / "data" / "rounds" / league / d.isoformat() / "round.json"
    return _parse(json.loads(p.read_text()))


@cache
def stations(league: str) -> pd.DataFrame:
    """Station metadata (lat/lon/elevation/network/day_offset/tier) from the
    PowderBench registry, indexed by station_id."""
    from dataclasses import asdict

    from powderbench.stations import load_stations

    df = pd.DataFrame([asdict(s) for s in load_stations(league)])
    df["featured"] = df["tier"].fillna("featured").eq("featured")
    return df.set_index("station_id")


@cache
def climatology(league: str) -> pd.DataFrame:
    """PowderBench's per-station day-of-year climatology (h24/h48/h72 p50,
    mean and P(>=6"))."""
    d = pb_checkout() / "data" / "climatology"
    p = d / f"{league}.csv.gz"
    if not p.exists():
        p = d / f"{league}.csv"
    if not p.exists():
        return pd.DataFrame(columns=["station_id", "doy"])
    return pd.read_csv(p)
