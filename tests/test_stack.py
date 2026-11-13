from datetime import date, timedelta

import numpy as np
import pandas as pd

from opm.models import stack

IDX = ["station_id", "horizon_h"]


def _setup(monkeypatch, tmp_path, n_rounds, ours_scale, base_scale):
    """Truth is t; we forecast ours_scale * t, the baseline base_scale * t."""
    rng = np.random.default_rng(1)
    truth, bases = [], {}
    (tmp_path / "stations").mkdir()
    for i in range(n_rounds):
        d = date(2026, 11, 1) + timedelta(days=i)
        t = pd.DataFrame({"station_id": [f"S{k}" for k in range(20)], "horizon_h": 24,
                          "truth_in": rng.gamma(1.0, 3.0, 20)})
        truth.append(t.assign(round_date=d))
        # model_in is deliberately absurd: the fit must read raw_in
        t.assign(raw_in=ours_scale * t.truth_in, model_in=40.0, snowfall_in=40.0).drop(columns="truth_in").to_csv(
            tmp_path / "stations" / f"{d.isoformat()}.csv", index=False)
        bases[d] = {"baseline-wpc": t.assign(snowfall_in=base_scale * t.truth_in).set_index(IDX)[["snowfall_in"]]}
    monkeypatch.setattr(stack, "HISTORY", tmp_path)
    monkeypatch.setattr(stack, "_featured", lambda league: set())
    monkeypatch.setattr(stack, "resolved_rounds", lambda league: pd.concat(truth, ignore_index=True))
    monkeypatch.setattr(stack, "_baselines", lambda league, d: bases.get(d, {}))


def test_blend_weight_is_learned_from_the_models_own_output(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, n_rounds=10, ours_scale=0.6, base_scale=1.4)  # the truth is halfway between
    cfg = stack.fit("stations", date(2026, 12, 1))
    assert cfg["partner"] == "baseline-wpc" and cfg["w"] == 0.5 and cfg["rounds"] == 10


def test_stays_off_without_enough_rounds(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, n_rounds=stack.MIN_ROUNDS - 1, ours_scale=0.6, base_scale=1.4)
    assert stack.fit("stations", date(2026, 12, 1)) is None


def test_stays_off_when_no_partner_helps(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, n_rounds=10, ours_scale=1.0, base_scale=1.4)  # we are already right
    assert stack.fit("stations", date(2026, 12, 1)) is None
