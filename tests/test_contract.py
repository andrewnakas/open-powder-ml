"""Contract tests against the real PowderBench checkout (work/powderbench):
whatever we build must pass the exact validator the auto-merge bot runs."""

import json

import numpy as np
import pandas as pd
import pytest

from opm import HORIZONS, QCOLS, pb_checkout
from opm.contract import _parse, stations
from opm.models import fallback
from opm.predict import sanitize

ROUNDS = sorted((pb_checkout() / "data" / "rounds").glob("*/*/round.json"))
pytestmark = pytest.mark.skipif(not ROUNDS, reason="no PowderBench checkout (scripts/checkout_powderbench.sh)")


def _latest_per_league():
    seen = {}
    for p in ROUNDS:
        seen[p.parent.parent.name] = p
    return list(seen.values())


@pytest.mark.parametrize("manifest", _latest_per_league(), ids=lambda p: p.parent.parent.name)
def test_tier0_submission_validates(manifest, tmp_path):
    from powderbench.validate import validate_submission

    r = _parse(json.loads(manifest.read_text()))
    reg = stations(r.league)
    ids = [s for s in r.stations if s in reg.index]
    rng = np.random.default_rng(1)
    rows = []
    for sid in ids:
        for h in HORIZONS:
            rows.append({"station_id": sid, "round_date": r.round_date, "horizon_h": h,
                         **{f"slr__m{i}": rng.gamma(0.5, 2) * h / 24 for i in range(5)}})
    W = pd.DataFrame(rows)
    pred = fallback.predict(W)
    df = sanitize(pred)
    out = tmp_path / "sub.csv"
    df.to_csv(out, index=False)
    res = validate_submission(out, league=r.league, round_stations=list(r.stations))
    assert res.ok, res.summary()
    assert res.coverage == pytest.approx(1.0)
    assert set(df.columns) == {"station_id", "horizon_h", "snowfall_in", *QCOLS, "prob_6in"}
