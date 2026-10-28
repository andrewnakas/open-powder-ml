"""What to do right now. Safe to call as often as cron fires (and cron fires
30 min to 5 h late, so it is called often).

Per open round:
  1. submit as soon as it opens: a guaranteed on-time file;
  2. update when a newer main NWP cycle (00Z / 12Z, all models in) becomes
     available, at most MAX_UPDATES times;
  2b. one final pass 3 to 1.25 h before the cutoff (the 12Z/18Z runs that
     land late in the day);
  3. never within submit.GUARD_MIN of the cutoff, never after.

State lives in history/<league>/<round>.json (committed by the workflow) and
is cross-checked against upstream main, so a lost commit can't make us skip
a round.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import ROOT
from .contract import Round, open_rounds
from .submit import GUARD_MIN

HISTORY = ROOT / "history"
CYCLE_LAG_H = 7  # a 00Z/12Z cycle is complete on Open-Meteo ~6-7 h later
MAX_UPDATES = 2
UPDATE_MIN_LEFT_H = 1.25  # leave the bot and its validation time to merge
FINAL_WINDOW_H = 3.0  # one last pass this close to the cutoff picks up the latest runs


def latest_cycle(now: datetime) -> str:
    c = now - timedelta(hours=CYCLE_LAG_H)
    c = c.replace(hour=0 if c.hour < 12 else 12, minute=0, second=0, microsecond=0)
    return c.strftime("%Y-%m-%dT%HZ")


def state_path(r: Round):
    return HISTORY / r.league / f"{r.round_date.isoformat()}.json"


def load_state(r: Round) -> dict:
    p = state_path(r)
    return json.loads(p.read_text()) if p.exists() else {}


def save_state(r: Round, st: dict) -> None:
    p = state_path(r)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, indent=1, sort_keys=True))


@dataclass
class Action:
    round: Round
    reason: str  # initial | update | resubmit


def due(now: datetime | None = None, leagues: list[str] | None = None, force: bool = False,
        on_upstream=None) -> list[Action]:
    """on_upstream(round) -> bool: whether our file is on upstream main
    (injected so tests and dry runs need no token)."""
    now = now or datetime.now(timezone.utc)
    cyc = latest_cycle(now)
    out = []
    for r in open_rounds(now, leagues):
        left = r.hours_left(now)
        if left * 60 < GUARD_MIN:
            continue
        st = load_state(r)
        if not st.get("submissions"):
            out.append(Action(r, "initial"))
        elif (on_upstream is not None and now - datetime.fromisoformat(st["submitted_utc"]) > timedelta(minutes=30)
              and not on_upstream(r)):
            out.append(Action(r, "resubmit"))  # never merged (bot down, rejected, lost): try again
        elif force or (st.get("cycle", "") < cyc and st.get("updates", 0) < MAX_UPDATES
                       and left >= UPDATE_MIN_LEFT_H):
            out.append(Action(r, "update"))
        elif (not st.get("final") and UPDATE_MIN_LEFT_H <= left <= FINAL_WINDOW_H
              and now - datetime.fromisoformat(st["submitted_utc"]) >= timedelta(hours=1.5)):
            out.append(Action(r, "final"))  # the freshest runs a cutoff-time forecast can use
    return out
