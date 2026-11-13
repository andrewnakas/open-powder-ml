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
