"""opm — the open-powder-ml command line (what the workflows run)."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import typer

from . import ROOT, use_pb_data

use_pb_data()
app = typer.Typer(add_completion=False, no_args_is_help=True)
LeaguesOpt = typer.Option(None, "--league", "-l", help="Limit to these leagues (repeatable)")


def _summary(md: str) -> None:
    print(md)
    p = os.environ.get("GITHUB_STEP_SUMMARY")
    if p:
        with open(p, "a") as f:
            f.write(md + "\n")


@app.command()
def rounds():
    """List the open rounds and their cutoffs."""
    from .contract import open_rounds

    now = datetime.now(timezone.utc)
    for r in open_rounds(now):
        print(f"{r.key:24s} cutoff {r.cutoff:%Y-%m-%d %H:%MZ} ({r.hours_left(now):5.1f} h)  "
              f"{len(r.stations)} stations, {len(r.featured)} featured")


@app.command()
def due(league: list[str] | None = LeaguesOpt):
    """What a forecast run would do now."""
    from .schedule import due as _due
    from .schedule import latest_cycle
    from .submit import upstream_file

    # with a token, also catch submissions that never merged
    on_up = (lambda r: upstream_file(r) is not None) if os.environ.get("POWDERBENCH_TOKEN") else None
    acts = _due(leagues=league, on_upstream=on_up)
    print(f"cycle {latest_cycle(datetime.now(timezone.utc))}")
    for a in acts:
        print(f"{a.reason:9s} {a.round.key} (cutoff {a.round.cutoff:%m-%d %H:%MZ})")
    if not acts:
        print("nothing due")


@app.command()
def run(
    league: list[str] | None = LeaguesOpt,
    dry_run: bool = typer.Option(False, help="Build and validate, don't submit"),
    force: bool = typer.Option(False, help="Treat every open round as due (still deadline-guarded)"),
    network: bool = typer.Option(True, help="Also forecast non-featured network stations"),
    budget: float = typer.Option(8000, help="Open-Meteo weighted-call budget for this run"),
    wait: int = typer.Option(240, help="Seconds to wait for the auto-merge per submission"),
):
    """Forecast and submit every due round."""
    from powderbench.validate import validate_submission

    from . import predict, schedule, submit

    on_up = None if dry_run else (lambda r: submit.upstream_file(r) is not None)
    acts = schedule.due(leagues=league, force=force, on_upstream=on_up)
    if not acts:
        print("nothing due")
        return
    failures = 0
    for a in acts:
        r = a.round
        try:
            res = predict.build(r, network=network, budget=budget)
            out = ROOT / "out" / r.league / f"{r.round_date}.csv"
            predict.write(res, out)
            v = validate_submission(out, league=r.league, round_stations=list(r.stations))
            if not v.ok:
                raise RuntimeError("validation failed:\n" + v.summary())
            line = (f"**{r.key}** ({a.reason}) · cutoff {r.cutoff:%m-%d %H:%MZ} · {res.info['rows']} rows · "
                    f"featured coverage {v.coverage:.0%} · tiers {res.info['tiers']} · "
                    f"Open-Meteo weight {res.info['openmeteo_weight']}")
            if res.info.get("quota_note"):
                line += f" · {res.info['quota_note']}"
            if dry_run:
                _summary(line + " · DRY RUN")
                continue
            hist = schedule.HISTORY / r.league / f"{r.round_date}.csv"
            hist.parent.mkdir(parents=True, exist_ok=True)
            res.raw.round(3).to_csv(hist, index=False)
            st = schedule.load_state(r)
            cyc = schedule.latest_cycle(datetime.now(timezone.utc))
            note = f"Model: {res.info['bundle'] or 'tier-0 ensemble'} · NWP cycle {cyc} · rows {res.info['rows']}"
            sub = submit.submit(r, out, note=note)
            st.update(
                submissions=st.get("submissions", 0) + 1,
                updates=st.get("updates", 0) + (a.reason == "update"),
                final=st.get("final", False) or a.reason == "final",
                cycle=cyc, submitted_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                pr=sub.pr_url, bundle=res.info["bundle"], tiers=res.info["tiers"],
            )
            schedule.save_state(r, st)
            merged = submit.wait_merged(r, out.read_text(), timeout_s=wait) if wait else None
            st["merged"] = merged
            schedule.save_state(r, st)
            _summary(f"{line} · [PR]({sub.pr_url}) · merged: {merged}")
        except submit.TooLate as e:
            _summary(f"**{r.key}** skipped: {e}")
        except Exception as e:  # one league failing must not stop the others
            failures += 1
            _summary(f"**{r.key}** FAILED: {type(e).__name__}: {e}")
    if failures:
        raise typer.Exit(1)


@app.command()
def backfill(
    league: list[str] | None = LeaguesOpt,
    budget: float = typer.Option(7000, help="Open-Meteo weighted-call budget"),
    network_sample: int = typer.Option(0, help="Also harvest N random non-featured stations per league"),
):
    """Harvest the next chunk of lead-matched archive forecasts (resumable)."""
    from . import backfill as bf
    from . import store

    store.pull("store", store.STORE)
    res = bf.run(league or ["stations", "observers", "alps", "era5", "resorts", "resorts-na"], budget, network_sample)
    _summary(f"backfill: {res}")


@app.command()
def train(
    bundle: str = typer.Option("nh", help="nh (stations+observers+alps), era5, resorts or resorts-na"),
    promote: bool = typer.Option(True, help="Upload as champion if it beats the current one"),
):
    """Train a bundle, score it on the held-out season, promote if better."""
    import pandas as pd

    from . import NH_LEAGUES, store
    from .dataset import training_table
    from .models import gbm

    store.pull("store", store.STORE)
    store.pull("models", store.MODELS)
    leagues = NH_LEAGUES if bundle == "nh" else (bundle,)
    tables = [t for t in (training_table(lg) for lg in leagues) if len(t)]
    if not tables:
        _summary(f"train {bundle}: no training data yet")
        return
    df = pd.concat(tables, ignore_index=True)
    path = store.MODELS / f"{bundle}.pkl"
    old = gbm.Bundle.load(path) if path.exists() else None
    b = gbm.fit(df, champion_params=getattr(old, "params", None) if old else None)
    _summary(f"train {bundle}: {len(df)} rows, seasons {sorted(df.season.unique())}, "
             f"{len(b.features)} features, rounds {b.rounds}")
    _summary("held-out: " + json.dumps({k: v for k, v in b.metrics.items() if k not in ("cons_slr_p99", "cv")}))
    if b.metrics.get("cv"):
        _summary("season CV (Powder Accuracy; raw multi-model consensus as the baseline): " + "; ".join(
            f"{r['label']} {r['powder_score']} vs raw {r['raw_consensus']} (folds {r['folds']})"
            for r in b.metrics["cv"]["all"]))
    cand = store.MODELS / f"{bundle}.candidate.pkl"
    b.save(cand)
    better = _better(b.metrics, old.metrics if old else None)
    _summary(f"champion: {'none' if old is None else old.metrics.get('powder_score')} -> "
             f"candidate {b.metrics.get('powder_score')}: {'PROMOTE' if better else 'keep champion'}")
    if better:
        cand.replace(path)
        (store.MODELS / f"{bundle}.json").write_text(json.dumps(b.metrics, indent=1, default=str))
        if promote:
            store.push("models", [path, store.MODELS / f"{bundle}.json"])


def _better(new: dict, old: dict) -> bool:
    """Promotion gate.

    With season CV (3+ seasons): the candidate was tuned against the
    champion's own settings on the same folds and retrained on more data, so
    it is promoted if its CV Powder Accuracy beats the raw multi-model consensus
    by 2+ points and isn't worse than the champion's settings re-scored on the
    same folds by > 0.5 point.

    Without CV: beat the raw consensus on the held-out season by 2+ points,
    and compare with the champion only on identical held-out rows."""
    if new.get("cv_powder_score") is not None:
        ref = new.get("cv_raw_consensus")
        if ref is not None and new["cv_powder_score"] < ref + 2:
            return False
        # vs the champion: its own settings were re-scored on these same folds
        # and rows (an old stored CV score was measured on different data)
        champ = new.get("cv_champion_params")
        return champ is None or new["cv_powder_score"] >= champ - 0.5
    ps_n, ref = new.get("powder_score"), new.get("powder_score_raw_consensus")
    if ps_n is None or (ref is not None and ps_n < ref + 2):
        return False
    # comparable only on the very same held-out rows (same label AND size;
    # a "-tail" holdout moves as the harvest grows)
    if not old or (old.get("holdout_season"), old.get("n")) != (new.get("holdout_season"), new.get("n")):
        return True
    pin_ok = new.get("pinball", 0) <= old.get("pinball", float("inf")) * 1.02
    return ps_n >= old.get("powder_score", -1e9) - 0.5 and pin_ok


@app.command()
def watchdog(hours: float = typer.Option(4.0, help="Alert for rounds this close to cutoff")):
    """Exit 1 (and print) if an open round near its cutoff lacks our merged file."""
    from .contract import open_rounds
    from .submit import upstream_file

    now = datetime.now(timezone.utc)
    missing = [r for r in open_rounds(now) if r.hours_left(now) <= hours and upstream_file(r) is None]
    for r in missing:
        print(f"MISSING {r.key}: cutoff {r.cutoff:%Y-%m-%d %H:%MZ} ({r.hours_left(now):.1f} h left)")
    if missing:
        raise typer.Exit(1)
    print("all open rounds near cutoff have our file")


@app.command()
def scoreboard(out: Path = typer.Option(Path("docs/scoreboard.md"))):
    """Render our standing from PowderBench's published leaderboards."""
    from .scoreboard import render

    md = render()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    readme = ROOT / "README.md"
    if readme.exists():
        txt = readme.read_text()
        a, b = "<!-- scoreboard:start -->", "<!-- scoreboard:end -->"
        if a in txt and b in txt:
            readme.write_text(txt.split(a)[0] + a + "\n" + md + "\n" + b + txt.split(b)[1])
    print(md)


if __name__ == "__main__":
    sys.exit(app())

