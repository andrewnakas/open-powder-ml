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
