from datetime import date, timedelta

import numpy as np
import pandas as pd

from opm import QCOLS
from opm.models import gbm


def _table(n_days=300, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n_days):
        d = date(2024, 11, 1) + timedelta(days=i)
        for sid in ("A", "B", "C"):
            for h in (24, 48, 72):
                cons = rng.gamma(0.4, 3) * h / 24
                rows.append({"station_id": sid, "round_date": d, "horizon_h": h, "cons_slr": cons,
                             "slr__m1": cons * 1.1, "slr__m2": cons * 0.9, "nmod": 2,
                             "truth_in": max(0.0, 0.7 * cons + rng.normal(0, 0.5)),
                             "season": 2024 if d < date(2025, 8, 1) else 2025, "aug": 0})
    return pd.DataFrame(rows)


def test_fit_predict_is_calibrated_and_monotone():
    df = _table()
    b = gbm.fit(df)
    assert b.metrics["mae"] > 0 and b.metrics["holdout_season"] == "2025"
    X = df[df.season == 2025].drop(columns=["truth_in"])
    out = gbm.predict(b, X)
    q = out[list(QCOLS)].to_numpy()
    assert (np.diff(q, axis=1) >= -1e-9).all()
    assert (out["snowfall_in"] == out["p50"]).all()
    # learned the 0.7 ratio: the median tracks 0.7 * consensus, not 1.0
    big = X["cons_slr"].to_numpy() > 4
    ratio = out.loc[big, "p50"].sum() / X.loc[big, "cons_slr"].sum()
    assert 0.55 < ratio < 0.85


def test_holdout_skips_a_season_that_has_barely_started(monkeypatch):
    monkeypatch.setattr(gbm, "MAX_ROUNDS", 100)
    df = _table(n_days=283)  # 273 days of season 2024, then 10 days of 2025 (3.5% of rows)
    b = gbm.fit(df)
    assert b.metrics["holdout_season"] == "2024-tail"  # not the 10-day season, and not all of 2024 either
    assert b.metrics["n_train"] > 0.6 * len(df)
    assert not any(hasattr(m, "train_set") for m in b.quantile_models.values())  # boosters don't pin their data


def _seasons(n=3):
    """Three full seasons of the synthetic table, with what experiments need."""
    parts = []
    for k in range(n):
        t = _table(n_days=120, seed=k)
        t["round_date"] = [d.replace(year=d.year + k) for d in t["round_date"]]
        t["season"] = 2024 + k
        parts.append(t)
    df = pd.concat(parts, ignore_index=True)
    df["league"], df["featured"], df["climo_p50"] = "stations", (df["station_id"] == "A").astype(int), 0.3
    return df


def test_experiments_score_variants_on_the_same_folds(monkeypatch):
    from opm.models import experiments

    monkeypatch.setattr(gbm, "MAX_ROUNDS", 60)
    df = _seasons()
    md = experiments.run(df, ["w_feat4", "resid", "seedbag", "moddrop", "f_spread"], gbm.PARAMS)
    rows = [ln for ln in md.splitlines() if ln.startswith("| ") and "variant" not in ln]
    assert [r.split("|")[1].strip() for r in rows] == ["base", "w_feat4", "resid", "seedbag", "moddrop", "f_spread"]
    assert all(r.split("|")[2].strip() not in ("", "None") for r in rows)  # every variant got a CV score
    assert not any(c.startswith("_") for c in gbm.feature_list(df))  # weight columns never become features


def test_experimental_columns_stay_out_until_asked_for():
    df = _seasons().assign(q25_slr=1.0, cons_tmin=-3.0)
    assert "q25_slr" not in gbm.feature_list(df) and "cons_tmin" not in gbm.feature_list(df)
    assert "q25_slr" in gbm.feature_list(df, ("q25_slr",))


def test_featured_weight_reaches_the_final_models(monkeypatch):
    monkeypatch.setattr(gbm, "MAX_ROUNDS", 60)
    df = _seasons()
    assert gbm.row_weight(df) is None  # weight 1: no weights at all
    monkeypatch.setattr(gbm, "FEATURED_WEIGHT", 4.0)
    w = gbm.row_weight(df)
    assert set(w) == {1.0, 4.0} and (w == 4.0).sum() == df["featured"].sum()
    b = gbm.fit(df)
    assert len(gbm.predict(b, df.drop(columns=["truth_in"]))) == len(df)
