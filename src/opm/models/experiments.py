"""Named variants of the median model, each judged by season CV against the
current settings on the same folds and rows (`experiment` command).

A variant is a function of the training table that returns what to change:
  df      a changed table (only when rows or columns must differ)
  extra   gbm.EXPERIMENTAL prefixes to let into the feature list
  drop    substrings of feature names to leave out
  params  LightGBM settings to override
  weight  name of a sample-weight column it added (starts with "_")
  resid   train on truth minus the raw consensus
  seeds   average this many models
Nothing here changes what `train` builds; a winner is moved into gbm.fit by hand.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import features
from . import gbm

ADOPT_GAIN = 0.3  # pooled-mean gain needed, with every fold up
LEAGUE_SLACK = 0.3  # and no league down by more than this
BINS = ((-1, 1, "0-1"), (1, 3, "1-3"), (3, 6, "3-6"), (6, 1e9, "6+"))


def _w_featured(k: float):
    def f(df):
        df[f"_w_feat{k:g}"] = 1.0 + (k - 1.0) * df["featured"].to_numpy()
        return {"weight": f"_w_feat{k:g}"}
    return f


def _w_recent(df):
    """Each season back from the newest counts 0.8x the one after it."""
    age = df["season"].max() - df["season"]
    df["_w_recent"] = np.power(0.8, age.to_numpy(dtype=float))
    return {"weight": "_w_recent"}


def _model_cols(df: pd.DataFrame, model: str) -> list[str]:
    return [c for c in df.columns if c.endswith(f"__{model}")]


def _resummarize(d: pd.DataFrame) -> pd.DataFrame:
    features.summarize(d)
    if "ref_snow" in d:  # the ensemble entry's reference includes the deterministic consensus
        from ..dataset import add_ref

        add_ref(d)
    return d


def _moddrop(df):
    """A fifth of the multi-model rows again with one model blanked, so a
    model that is missing live is something the trees have seen. The copies
    are marked aug=1: trained on, never scored."""
    models = sorted({c.split("__", 1)[1] for c in df.columns if c.startswith("slr__")})
    pool = df[(df["nmod"] >= 3) & ((df["aug"] == 0) if "aug" in df else True)]
    sub = pool.sample(frac=0.2, random_state=3).copy()
    rng = np.random.default_rng(3)
    have = np.column_stack([sub[f"slr__{m}"].notna().to_numpy() for m in models])
    pick = np.argmax(np.where(have, rng.random(have.shape), -1.0), axis=1)
    for j, m in enumerate(models):
        sub.loc[pick == j, _model_cols(sub, m)] = np.nan
    sub = _resummarize(sub)
    sub["aug"] = 1
    out = pd.concat([df.assign(aug=df["aug"] if "aug" in df else 0), sub], ignore_index=True)
    return {"df": out}


def _without(model: str):
    def f(df):
        d = df.copy()
        d[_model_cols(d, model)] = np.nan
        return {"df": _resummarize(d)}
    return f


VARIANTS = {
    "w_feat2": _w_featured(2.0),
    "w_feat4": _w_featured(4.0),
    "w_recent": _w_recent,
    "resid": lambda df: {"resid": True},
    "seedbag": lambda df: {"seeds": 3},
    "moddrop": _moddrop,
    "no_hrrr": _without("ncep_hrrr_conus"),
    "p_lr02": lambda df: {"params": {"learning_rate": 0.02}},
    "p_ff5": lambda df: {"params": {"feature_fraction": 0.5}},
    "p_l255": lambda df: {"params": {"num_leaves": 255, "min_child_samples": 150}},
    "f_days": lambda df: {"extra": ("cons_slr_d", "cons_prcp_d")},
    "f_spread": lambda df: {"extra": ("q25_slr", "q75_slr")},
    "f_tmin": lambda df: {"extra": ("cons_tmin",)},
    "f_all": lambda df: {"extra": gbm.EXPERIMENTAL},
}


def _pa(d: pd.DataFrame) -> float | None:
    d = d.dropna(subset=["climo_p50"])
    ref = float((d["climo_p50"] - d["truth_in"]).abs().sum())
    return round(100.0 * (1.0 - float((d["pred"] - d["truth_in"]).abs().sum()) / ref), 1) if ref > 0 else None


def breakdown(oof: pd.DataFrame) -> dict:
    """Pooled Powder Accuracy by league and horizon; 24 h MAE by truth amount."""
    out = {}
    if "league" in oof:
        out.update({f"{lg}": _pa(g) for lg, g in oof.groupby("league")})
    out.update({f"{int(h)}h": _pa(g) for h, g in oof.groupby("horizon_h")})
    d = oof[oof["horizon_h"] == 24]
    err = (d["pred"] - d["truth_in"]).abs()
    for lo, hi, name in BINS:
        m = (d["truth_in"] > lo) & (d["truth_in"] <= hi) if lo >= 0 and lo != -1 else d["truth_in"] <= hi
        out[f"mae {name}"] = round(float(err[m].mean()), 2) if m.any() else None
    return out


def evaluate(df: pd.DataFrame, name: str, params: dict, folds: list) -> dict:
    spec = VARIANTS[name](df) if name != "base" else {}
    d = spec.get("df", df)
    feats = [f for f in gbm.feature_list(d, spec.get("extra", ()))
             if not any(x in f for x in spec.get("drop", ()))]
    oof: list = []
    r = gbm.cross_validate(d, feats, {**params, **spec.get("params", {})}, folds, weight=spec.get("weight"),
                           resid=spec.get("resid", False), seeds=spec.get("seeds", 1), oof=oof)
    r.update(name=name, n_features=len(feats), by=breakdown(pd.concat(oof, ignore_index=True)) if oof else {})
    print(f"  {name}: {r['powder_score']} per fold {r['per_fold']} {r['by']}", flush=True)
    return r


def verdict(base: dict, r: dict, leagues: list[str]) -> str:
    if r["powder_score"] is None or base["powder_score"] is None:
        return "no score"
    folds_up = all(a is not None and b is not None and a > b for a, b in zip(r["per_fold"], base["per_fold"]))
    lg_ok = all((r["by"].get(lg) or 0) >= (base["by"].get(lg) or 0) - LEAGUE_SLACK for lg in leagues)
    return "adopt" if r["powder_score"] - base["powder_score"] >= ADOPT_GAIN and folds_up and lg_ok else "no"


def run(df: pd.DataFrame, names: list[str], params: dict) -> str:
    unknown = [n for n in names if n not in VARIANTS]
    if unknown:
        raise ValueError(f"unknown variants {unknown}; have {list(VARIANTS)}")
    df = gbm.add_oof_ratio(df)
    folds = gbm.cv_folds(df)
    if not folds:
        return "experiment: fewer than 3 full seasons, no CV"
    res = [evaluate(df, n, params, folds) for n in ["base", *names]]
    base, cols = res[0], list(res[0]["by"])
    leagues = sorted(df["league"].unique()) if "league" in df else []
    head = ["variant", "CV", "delta", *[str(f) for f in folds], *cols, "features", "verdict"]
    lines = [f"experiment on {len(df)} rows, folds {folds}, settings {params}", "",
             "| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in res:
        delta = "" if r is base or r["powder_score"] is None else f"{r['powder_score'] - base['powder_score']:+.2f}"
        cells = [r["name"], r["powder_score"], delta, *[None if x is None else round(x, 1) for x in r["per_fold"]],
                 *[r["by"].get(c) for c in cols], r["n_features"], "" if r is base else verdict(base, r, leagues)]
        lines.append("| " + " | ".join("" if c is None else str(c) for c in cells) + " |")
    return "\n".join(lines)
