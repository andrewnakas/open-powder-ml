"""Tier 1: LightGBM quantile regression on multi-model snow-physics features.

- one quantile model per level (p10..p90); snowfall_in = p50 (the
  MAE-optimal point)
- a binary model for P(>= 6" in 24 h), trained on the 24 h rows
- a per-station bias feature (shrunk truth/consensus ratio), computed
  out-of-fold by season so it can't leak the target
- quantiles rescaled on a held-out season so each level's empirical coverage
  matches its nominal value (multiplicative, so dry rows stay at 0)
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from .. import POWDER_IN, QCOLS, QUANTILES

KEYS = ("station_id", "round_date", "horizon_h")
REF = "cons_slr"  # the raw consensus every gain is measured against
NON_FEATURES = {"station_id", "round_date", "truth_in", "league", "season", "aug", "featured"}
RATIO_K = 4.0  # inches of pseudo-count shrinking a station's ratio toward 1
PARAMS = dict(learning_rate=0.03, num_leaves=31, min_child_samples=40, feature_fraction=0.8,
              bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1)
MAX_ROUNDS = 1500
# Candidate settings tuned by season-wise cross-validation (the champion's own
# settings always join the comparison, so a retrain can only move to
# something that scores better on the same folds).
GRID = [
    PARAMS,
    {**PARAMS, "num_leaves": 15, "min_child_samples": 100, "learning_rate": 0.05},
    {**PARAMS, "num_leaves": 63, "min_child_samples": 60},
    {**PARAMS, "num_leaves": 31, "min_child_samples": 200, "lambda_l2": 10.0, "learning_rate": 0.05},
    {**PARAMS, "num_leaves": 127, "min_child_samples": 100, "feature_fraction": 0.6},
]
CV_FOLDS = 3  # the most recent seasons, each held out in turn


@dataclass
class Bundle:
    features: list[str]
    quantile_models: dict[float, lgb.Booster]
    clf: lgb.Booster | None
    scale: dict[int, dict[float, float]]
    stn_ratio: dict[str, float]
    rounds: dict[str, int]
    metrics: dict = field(default_factory=dict)
    params: dict | None = None
    trained_through: str = ""

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pickle.dumps(self))

    @staticmethod
    def load(path: Path) -> Bundle:
        return pickle.loads(path.read_bytes())


def station_ratio(df: pd.DataFrame) -> dict[str, float]:
    h = df[(df["horizon_h"] == 24) & (df["aug"] == 0 if "aug" in df else True)]
    g = h.groupby("station_id").agg(t=("truth_in", "sum"), c=("cons_slr", "sum"))
    return ((g["t"] + RATIO_K) / (g["c"] + RATIO_K)).clip(0.25, 4.0).to_dict()


def add_oof_ratio(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["stn_ratio"] = np.nan
    for s in df["season"].unique():
        other = df[df["season"] != s]
        if other.empty:
            continue
        m = df["season"] == s
        df.loc[m, "stn_ratio"] = df.loc[m, "station_id"].map(station_ratio(other))
    return df


def feature_list(df: pd.DataFrame) -> list[str]:
    cols = [c for c in df.columns if c not in NON_FEATURES and pd.api.types.is_numeric_dtype(df[c])]
    return [c for c in cols if df[c].notna().any()]


def _train(X, y, obj: dict, Xv=None, yv=None, rounds: int | None = None, params: dict | None = None):
    params = params or PARAMS
    ds = lgb.Dataset(X, y, free_raw_data=False)
    if rounds is None and Xv is not None:
        dv = lgb.Dataset(Xv, yv, reference=ds)
        b = lgb.train({**params, **obj}, ds, MAX_ROUNDS, valid_sets=[dv],
                      callbacks=[lgb.early_stopping(100, verbose=False)])
        return b, b.best_iteration or MAX_ROUNDS
    b = lgb.train({**params, **obj}, ds, rounds or 600)
    return b, rounds or 600


def _ps(pred: np.ndarray, d: pd.DataFrame) -> float | None:
    from ..backtest import powder_score

    m = d["climo_p50"].notna().to_numpy() if "climo_p50" in d else np.zeros(len(d), bool)
    if not m.any():
        return None
    y = d["truth_in"].to_numpy()[m]
    return powder_score(float(np.abs(pred[m] - y).mean()), float(np.abs(d["climo_p50"].to_numpy()[m] - y).mean()))


def cross_validate(df: pd.DataFrame, feats: list[str], params: dict, folds: list) -> dict:
    """Season-wise CV of the median model (the MAE-optimal point forecast that
    Powder Accuracy judges): each fold season is held out, the rest train."""
    scores, raw = [], []
    for s in folds:
        tr, va = df[df["season"] != s], df[df["season"] == s]
        if "aug" in va:
            va = va[va["aug"] == 0]
        if "featured" in va and va["featured"].any():  # score like the headline board
            va = va[va["featured"] == 1]
        if tr.empty or va.empty:
            continue
        m, _ = _train(tr[feats], tr["truth_in"], {"objective": "quantile", "alpha": 0.5},
                      va[feats], va["truth_in"], params=params)
        scores.append(_ps(np.clip(m.predict(va[feats], num_iteration=m.best_iteration or None), 0, None), va))
        raw.append(_ps(va[REF].to_numpy(), va.dropna(subset=[REF])) if REF in va else None)
    ok = [x for x in scores if x is not None]
    rk = [x for x in raw if x is not None]
    return {"folds": [str(f) for f in folds], "per_fold": scores,
            "powder_score": round(float(np.mean(ok)), 2) if ok else None,
            "raw_consensus": round(float(np.mean(rk)), 2) if rk else None}


def tune(df: pd.DataFrame, feats: list[str], champion: dict | None = None) -> tuple[dict, dict]:
    """Pick the settings with the best mean CV Powder Accuracy."""
    counts = df["season"].value_counts()
    # folds: the most recent seasons with a real share of the data (a season
    # that has barely started would make a noisy fold)
    seasons = sorted(s for s, n in counts.items() if n >= 0.05 * len(df))
    if len(seasons) < 3:
        return PARAMS, {}
    folds = seasons[-CV_FOLDS:]
    cands = list(GRID) + ([champion] if champion and champion not in GRID else [])
    results = []
    for i, p in enumerate(cands):
        r = cross_validate(df, feats, p, folds)
        r["label"] = "champion" if champion is not None and p == champion else f"grid{i}"
        print(f"  cv {r['label']}: {r['powder_score']} (raw {r['raw_consensus']}) per fold {r['per_fold']}",
              flush=True)
        results.append((r["powder_score"] if r["powder_score"] is not None else -1e9, p, r))
    best = max(results, key=lambda x: x[0])
    return best[1], {"best": best[2], "all": [r for _, _, r in results]}


def _predict_raw(models, clf, X: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=X.index)
    for a, m in models.items():
        out[a] = np.clip(m.predict(X, num_iteration=m.best_iteration or None), 0, 200)
    out["prob"] = np.clip(clf.predict(X), 0.001, 0.999) if clf is not None else np.nan
    return out


def _fit_scale(raw: pd.DataFrame, y: pd.Series, h: pd.Series) -> dict[int, dict[float, float]]:
    """Per horizon and level, the multiplier s for which P(y <= s*q) = level."""
    grid = np.linspace(0.5, 2.0, 61)
    scale = {}
    for hh in sorted(h.unique()):
        m = (h == hh).to_numpy()
        scale[int(hh)] = {}
        for a in QUANTILES:
            q = raw.loc[m, a].to_numpy()
            yy = y[m].to_numpy()
            wet = q > 0.05
            if wet.sum() < 50:
                scale[int(hh)][a] = 1.0
                continue
            cov = np.array([(yy[wet] <= s * q[wet]).mean() for s in grid])
            scale[int(hh)][a] = float(grid[np.argmin(np.abs(cov - a))]) if a != 0.5 else 1.0
    return scale


def finish(raw: pd.DataFrame, h: pd.Series, scale) -> pd.DataFrame:
    q = raw[list(QUANTILES)].copy()
    for a in QUANTILES:
        q[a] = q[a] * h.map(lambda x: scale.get(int(x), {}).get(a, 1.0)).to_numpy()
    arr = np.sort(np.clip(q.to_numpy(), 0, 200), axis=1)
    out = pd.DataFrame(arr, columns=list(QCOLS), index=raw.index)
    out["snowfall_in"] = out["p50"]
    out["prob_6in"] = raw["prob"]
    return out


def fit(df: pd.DataFrame, holdout=None, champion_params: dict | None = None) -> Bundle:
    """Settings are tuned by season-wise CV; then the last season is held out
    for early stopping, quantile calibration and metrics, and the final models
    are refit on everything with the chosen round counts."""
    df = add_oof_ratio(df)
    feats = feature_list(df)
    params, cv = tune(df, feats, champion_params)
    seasons = sorted(df["season"].unique())
    if len(seasons) > 1 or holdout is not None:
        holdout = holdout if holdout is not None else seasons[-1]
        is_va = df["season"] == holdout
    else:  # a single season so far: hold out its last 30% of round dates
        cut = sorted(df["round_date"].unique())[int(0.7 * df["round_date"].nunique())]
        is_va = df["round_date"] >= cut
        holdout = f"{seasons[0]}-tail"
    tr, va = df[~is_va], df[is_va]

    rounds, models_va = {}, {}
    for a in QUANTILES:
        m, n = _train(tr[feats], tr["truth_in"], {"objective": "quantile", "alpha": a},
                      va[feats] if va is not None else None, va["truth_in"] if va is not None else None,
                      params=params)
        rounds[str(a)], models_va[a] = n, m
    t24 = tr[tr["horizon_h"] == 24]
    v24 = va[va["horizon_h"] == 24] if va is not None else None
    clf_va, n = _train(t24[feats], (t24["truth_in"] >= POWDER_IN).astype(int), {"objective": "binary"},
                       v24[feats] if v24 is not None else None,
                       (v24["truth_in"] >= POWDER_IN).astype(int) if v24 is not None else None, params=params)
    rounds["clf"] = n

    scale, metrics = {}, {}
    if "aug" in va:  # calibrate and score on the full-model rows only
        va = va[va["aug"] == 0]
    va_all = va
    if "featured" in va and va["featured"].any():
        va = va[va["featured"] == 1]
    if va is not None and len(va):
        raw_all = _predict_raw(models_va, clf_va, va_all[feats])
        scale = _fit_scale(raw_all, va_all["truth_in"], va_all["horizon_h"])
        raw = _predict_raw(models_va, clf_va, va[feats])
        pred = finish(raw, va["horizon_h"], scale)
        from ..backtest import score

        metrics = score(va.assign(**{c: pred[c] for c in pred.columns}))
        metrics["holdout_season"] = str(holdout)
        metrics["n_train"] = int(len(tr))
    if cv:
        metrics["cv_powder_score"] = cv["best"]["powder_score"]
        metrics["cv_raw_consensus"] = cv["best"]["raw_consensus"]
        metrics["cv"] = cv
        champ = next((r for r in cv["all"] if r.get("label") == "champion"), None)
        metrics["cv_champion_params"] = champ["powder_score"] if champ else None
    metrics["cons_slr_p99"] = {str(int(h)): float(g.quantile(0.99))
                               for h, g in df.groupby("horizon_h")["cons_slr"]}

    final_q = {a: _train(df[feats], df["truth_in"], {"objective": "quantile", "alpha": a},
                         rounds=max(50, int(rounds[str(a)] * 1.1)), params=params)[0] for a in QUANTILES}
    d24 = df[df["horizon_h"] == 24]
    final_clf = _train(d24[feats], (d24["truth_in"] >= POWDER_IN).astype(int), {"objective": "binary"},
                       rounds=max(50, int(rounds["clf"] * 1.1)), params=params)[0]
    return Bundle(features=feats, quantile_models=final_q, clf=final_clf, scale=scale,
                  stn_ratio=station_ratio(df), rounds=rounds, metrics=metrics, params=params,
                  trained_through=str(max(df["round_date"])))


def predict(b: Bundle, X: pd.DataFrame) -> pd.DataFrame:
    X = X.copy()
    X["stn_ratio"] = X["station_id"].map(b.stn_ratio)
    for f in b.features:
        if f not in X.columns:
            X[f] = np.nan
    raw = _predict_raw(b.quantile_models, b.clf, X[b.features])
    out = finish(raw, X["horizon_h"], b.scale)
    return pd.concat([X[list(KEYS)].reset_index(drop=True), out.reset_index(drop=True)], axis=1)
