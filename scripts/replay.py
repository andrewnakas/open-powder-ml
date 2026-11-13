"""Score our resolved rounds against the public baselines, board-style.

    .venv/bin/python scripts/replay.py -l stations observers [--simulate] [--all-stations]
"""

from __future__ import annotations

import argparse

from opm import replay

ap = argparse.ArgumentParser()
ap.add_argument("-l", "--leagues", nargs="+", default=["stations", "observers", "alps", "resorts-na"])
ap.add_argument("--simulate", action="store_true", help="walk the shipped bias + stack chain forward on raw_in")
ap.add_argument("--all-stations", action="store_true", help="network rows too (the board uses featured only)")
a = ap.parse_args()
for lg in a.leagues:
    replay.report(lg, do_simulate=a.simulate, featured=not a.all_stations)
