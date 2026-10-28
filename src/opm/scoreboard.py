"""Our standing, read from PowderBench's published leaderboards
(data/results/<league>/leaderboard.json in the checkout)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import TEAM, pb_checkout

SHOW = 6


def _fmt(x, nd=1):
    return "–" if x is None else f"{x:.{nd}f}"


def render() -> str:
    lines = [f"_Updated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from PowderBench leaderboards._", ""]
    for p in sorted((pb_checkout() / "data" / "results").glob("*/leaderboard.json")):
        lb = json.loads(p.read_text())
        rows = lb.get("season") or []
        if not rows:
            continue
        ours = next((r for r in rows if r["team"] == TEAM), None)
        head = f"### {lb['league']} — " + (
            f"**#{ours.get('rank', '?')}** of {len(rows)} (Powder Accuracy {_fmt(ours.get('powder_score'))}, "
            f"{ours['rounds']} rounds{'' if ours.get('eligible') else ', not yet ranked'})"
            if ours else "not entered yet")
        lines += [head, "", "| # | team | Powder Accuracy | MAE | pinball | Brier₆ | rounds |",
                  "|---|---|---|---|---|---|---|"]
        top = rows[:SHOW] + ([ours] if ours and ours not in rows[:SHOW] else [])
        for r in top:
            name = f"**{r['team']}**" if r["team"] == TEAM else r["team"]
            lines.append(f"| {r.get('rank', '')} | {name} | {_fmt(r.get('powder_score'))} | {_fmt(r.get('mae'), 2)} "
                         f"| {_fmt(r.get('pinball'), 3)} | {_fmt(r.get('brier6'), 3)} | {r.get('rounds')} |")
        lines.append("")
    return "\n".join(lines)
