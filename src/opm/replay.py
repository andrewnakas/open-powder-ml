"""Replay of our resolved rounds, scored the way the PowderBench board scores.

The headline board is Powder Accuracy = 100 * (1 - MAE / MAE_climatology) on
the featured stations. This module joins, for every resolved round we have a
history file for:

- our model's own output (`raw_in`) and what we submitted (`snowfall_in`),
- every public `baseline-*.csv` of that round,
- the round's truth and the climatology baseline,

and reports each source next to ours *on identical rows*, pooled over rounds
(sum of errors over sum of climatology errors), per horizon and per truth
amount. `simulate` re-runs the post-processing chain (online bias, stacking)
on the stored `raw_in`, walking forward and only using truth that had
resolved by each round's cutoff, so a change to that chain can be judged on
real rounds without leaking the answer.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import ROOT, pb_checkout
from .contract import stations
from .truth import resolved_rounds

HISTORY = ROOT / "history"
KEY = ["round_date", "station_id", "horizon_h"]
BINS = (("0", -0.001, 0.0), ("0-1", 0.0, 1.0), ("1-3", 1.0, 3.0), ("3-6", 3.0, 6.0), ("6+", 6.0, 1e9))
# days after round day D before its truth is public (resolve day + 1): a round
# resolved on D+3 can first inform the round whose cutoff is D+4
RESOLVE_LAG = {"stations": 4, "observers": 7, "alps": 5, "resorts-na": 5, "resorts": 4, "era5": 9}


def load(league: str, featured: bool = True, since: date | None = None) -> pd.DataFrame:
    """One row per (round, station, horizon) with truth: columns `truth`,
    `climo`, `raw`, `submitted` and one per public baseline (short name)."""
    tr = resolved_rounds(league, since)
    if featured:
        st = stations(league)
        tr = tr[tr["station_id"].isin(set(st.index[st["featured"]]))]
    frames = []
    for d in sorted(tr["round_date"].unique()):
        t = tr[tr["round_date"] == d].set_index(["station_id", "horizon_h"])["truth_in"].rename("truth")
        cols = [t]
        h = HISTORY / league / f"{d.isoformat()}.csv"
        if h.exists():
            o = pd.read_csv(h).set_index(["station_id", "horizon_h"])
            if "raw_in" in o:
                cols.append(o["raw_in"].rename("raw"))
            cols.append(o["snowfall_in"].rename("submitted"))
        for p in sorted((pb_checkout() / "data" / "submissions" / league / d.isoformat()).glob("baseline-*.csv")):
            try:
                b = pd.read_csv(p).set_index(["station_id", "horizon_h"])["snowfall_in"]
            except Exception:
                continue
            name = p.stem.removeprefix("baseline-")
            cols.append(b.rename("climo" if name == "climatology" else name))
        j = pd.concat(cols, axis=1).dropna(subset=["truth"]).reset_index()
        j.insert(0, "round_date", d)
        frames.append(j)
    if not frames:
        return pd.DataFrame(columns=[*KEY, "truth"])
    df = pd.concat(frames, ignore_index=True)
    for c in ("climo", "raw", "submitted"):
        if c not in df:
            df[c] = np.nan
    return df


def pooled(df: pd.DataFrame, col: str) -> dict:
    """Pooled Powder Accuracy of `col` on rows where it and climatology exist."""
    g = df.dropna(subset=[col, "climo", "truth"])
    if g.empty:
        return {"n": 0, "rounds": 0, "mae": None, "powder": None}
    err = (g[col] - g["truth"]).abs().sum()
    ce = (g["climo"] - g["truth"]).abs().sum()
    return {"n": len(g), "rounds": int(g["round_date"].nunique()), "mae": float(err / len(g)),
            "powder": float(100 * (1 - err / ce)) if ce else None}


def sources(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in (*KEY, "truth", "climo")]


def versus(df: pd.DataFrame, ours: str = "raw") -> pd.DataFrame:
    """Every source against `ours` on the rows both cover."""
    rows = []
    for c in sources(df):
        g = df.dropna(subset=[c, ours, "climo", "truth"])
        if g.empty:
            continue
        a, b = pooled(g, c), pooled(g, ours)
        rows.append({"source": c, "rounds": a["rounds"], "n": a["n"], "mae": a["mae"], "powder": a["powder"],
                     f"{ours}_mae": b["mae"], f"{ours}_powder": b["powder"]})
    out = pd.DataFrame(rows)
    return out.sort_values("powder", ascending=False, na_position="last").reset_index(drop=True) if len(out) else out


def by_bin(df: pd.DataFrame, cols: list[str], horizon: int | None = 24) -> pd.DataFrame:
    """MAE and mean forecast by truth amount, on rows every column covers."""
    g = df.dropna(subset=[*cols, "truth"])
    if horizon:
        g = g[g["horizon_h"] == horizon]
    total = {c: (g[c] - g["truth"]).abs().sum() for c in cols}
    rows = []
    for name, lo, hi in BINS:
        s = g[(g["truth"] > lo) & (g["truth"] <= hi)] if name != "6+" else g[g["truth"] > lo]
        if s.empty:
            continue
        r = {"truth_in": name, "n": len(s), "mean_truth": s["truth"].mean()}
        for c in cols:
            e = (s[c] - s["truth"]).abs()
            r[f"{c}_mae"] = e.mean()
            r[f"{c}_mean"] = s[c].mean()
            r[f"{c}_err_share"] = e.sum() / total[c] if total[c] else np.nan
        rows.append(r)
    return pd.DataFrame(rows)


def by_horizon(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    g = df.dropna(subset=[*cols, "climo", "truth"])
    rows = []
    for h, s in g.groupby("horizon_h"):
        r = {"horizon_h": h, "n": len(s)}
        for c in cols:
            r[c] = pooled(s, c)["powder"]
        rows.append(r)
    return pd.DataFrame(rows)


def simulate(league: str, df: pd.DataFrame, chain) -> pd.Series:
    """Walk forward over the rounds in `df`. `chain(league, round_frame,
    known_before)` returns the p50 forecast for one round's rows, having seen
    only truth of rounds before `known_before`."""
    lag = timedelta(days=RESOLVE_LAG.get(league, 5))
    out = []
    for d, g in df.groupby("round_date"):
        out.append(pd.Series(np.asarray(chain(league, g, d - lag), dtype=float), index=g.index))
    return pd.concat(out).reindex(df.index) if out else pd.Series(dtype=float)


def current_chain(league: str, g: pd.DataFrame, known_before: date) -> np.ndarray:
    """The shipped post-processing on p50: online bias, then the one-partner stack."""
    from .models import online, stack

    lg, st = online.factors(league, known_before)
    x = g["raw"] * (lg * g["station_id"].map(st).fillna(1.0)).clip(*online.CLIP)
    cfg = stack.fit(league, known_before)
    if cfg:
        name = cfg["partner"].removeprefix("baseline-")
        if name in g:
            b = g[name]
            x = x.where(b.isna(), (1 - cfg["w"]) * x + cfg["w"] * b)
    return x.clip(0, 200).to_numpy()


def report(league: str, do_simulate: bool = False, featured: bool = True) -> None:
    df = load(league, featured=featured)
    df = df[df["raw"].notna()] if "raw" in df else df.iloc[:0]
    if df.empty:
        print(f"{league}: no resolved rounds with history")
        return
    if do_simulate:
        df = df.assign(sim=simulate(league, df, current_chain))
    pd.set_option("display.width", 200, "display.max_columns", 30, "display.float_format", "{:.3f}".format)
    print(f"\n== {league}: {df['round_date'].nunique()} rounds {df['round_date'].min()}..{df['round_date'].max()}, "
          f"{len(df)} {'featured ' if featured else ''}rows ==")
    print(versus(df).to_string(index=False))
    main = [c for c in ("raw", "sim", "consensus", "wpc") if c in df and df[c].notna().any()]
    print("\npowder by horizon (shared rows):")
    print(by_horizon(df, main).to_string(index=False))
    print("\n24 h by truth amount (shared rows):")
    print(by_bin(df, main).to_string(index=False))
