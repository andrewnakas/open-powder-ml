from datetime import date

import numpy as np
import pandas as pd

from opm import replay
from opm.models import blend


def _frame():
    rows = []
    for i in range(6):
        for s, truth in (("A", 2.0), ("B", 0.0)):
            rows.append({"round_date": date(2026, 11, 1 + i), "station_id": s, "horizon_h": 24, "truth": truth,
                         "climo": 0.0, "raw": truth * 0.5, "good": truth, "partial": truth if s == "A" else np.nan})
    return pd.DataFrame(rows)


def test_pooled_is_sum_of_errors_over_sum_of_climatology_errors():
    df = _frame()
    assert replay.pooled(df, "raw")["powder"] == 50.0
    assert replay.pooled(df, "good")["powder"] == 100.0
    v = replay.versus(df).set_index("source")
    assert v.loc["partial", "n"] == 6 and v.loc["partial", "raw_powder"] == 50.0  # ours on the same rows


def test_bins_split_the_error():
    b = replay.by_bin(_frame(), ["raw"]).set_index("truth_in")
    assert b.loc["0", "raw_mae"] == 0.0 and b.loc["1-3", "raw_err_share"] == 1.0


def test_simulate_only_sees_resolved_rounds():
    seen = []

    def chain(league, g, known_before):
        seen.append((g["round_date"].iloc[0], known_before))
        return g["raw"].to_numpy()

    out = replay.simulate("stations", _frame(), chain)
    assert len(out) == 12 and all((d - kb).days == replay.RESOLVE_LAG["stations"] for d, kb in seen)


def test_blend_moves_toward_a_better_source_and_handles_gaps():
    df = _frame()
    w = blend.fit(df[["round_date", "station_id", "horizon_h", "truth", "raw", "good"]])
    assert w["good"] > 0.5 and abs(sum(w.values()) - 1) < 1e-6 and w["raw"] > 0  # shrunk toward ours
    p = blend.predict(df, {"raw": 0.5, "partial": 0.5})
    assert p[1] == df["raw"].iloc[1]  # the partner is absent for station B: our own forecast


def test_snow_probe_compares_on_covered_rows(monkeypatch):
    import numpy as np
    import pandas as pd

    from opm.models import gbm

    rng = np.random.default_rng(0)
    n = 600
    df = pd.DataFrame({"season": np.repeat([2023, 2024, 2025], n // 3), "x": rng.normal(size=n),
                       "truth_in": rng.gamma(1.0, 1.0, n), "climo_p50": 0.5})
    df["cons_snow"] = df["truth_in"] + rng.normal(0, 0.1, n)
    df["nsnow"] = np.where(np.arange(n) % 2 == 0, 4, 0)
    df.loc[df["nsnow"] == 0, "cons_snow"] = np.nan
    monkeypatch.setattr(gbm, "SNOW_PROBE_MIN_ROWS", 100)
    monkeypatch.setattr(gbm, "MAX_ROUNDS", 60)
    params = {**gbm.PARAMS, "min_child_samples": 5, "num_leaves": 7, "learning_rate": 0.2}
    out = gbm.snow_probe(df, ["x"], params, ["2023", "2024", "2025"])
    assert out["rows"] == n // 2 and out["with"] > out["without"]


def test_stack_partner_is_capped_at_our_p90():
    import pandas as pd

    from opm.models.stack import capped

    base = pd.Series([11.0, 0.2, 5.0, 3.0])
    out = capped(base, pd.Series([0.0, 0.1, 4.0, 1.0]), pd.Series([0.5, 0.6, 3.0, float("nan")]))
    assert out.tolist() == [0.5, 0.2, 4.0, 3.0]


def test_snowpack_falls_back_a_day_when_the_reading_is_not_out():
    from datetime import date

    import pandas as pd

    from opm import snowpack

    obs = pd.DataFrame({"station_id": "1:CO:SNTL", "date": pd.date_range("2026-11-06", "2026-11-12"),
                        "SNWD": [10, 10, 12, 12, 15, 15, 20], "WTEQ": 3.0, "TAVG": 25.0})
    keys = pd.DataFrame({"station_id": ["1:CO:SNTL"] * 2, "round_date": [date(2026, 11, 14), date(2026, 11, 15)]})
    f = snowpack.features(keys, obs).set_index("round_date")
    assert f.loc[date(2026, 11, 14), "sp_depth"] == 20  # D-2 = 11-12 exists
    assert f.loc[date(2026, 11, 15), "sp_depth"] == 20  # D-2 = 11-13 not out: 11-12 stands in
    assert f.loc[date(2026, 11, 15), "sp_new1"] == 5
