from datetime import date, timedelta

import pandas as pd

from opm.models import online


def _history(n_rounds, truth, forecast, stations=("A", "B")):
    """Every station forecast `forecast` inches and got `truth`, n_rounds times."""
    return pd.DataFrame([{"station_id": s, "round_date": date(2026, 11, 1) + timedelta(days=i), "horizon_h": 24,
                          "snowfall_in": forecast, "truth_in": truth} for i in range(n_rounds) for s in stations])


def _pred():
    return pd.DataFrame({"station_id": ["A", "NEW"], "horizon_h": [24, 24], "snowfall_in": [10.0, 10.0],
                         "p10": [5.0] * 2, "p25": [8.0] * 2, "p50": [10.0] * 2, "p75": [12.0] * 2, "p90": [15.0] * 2})


def test_a_few_rounds_change_nothing(monkeypatch):
    monkeypatch.setattr(online, "our_history", lambda league, before: _history(4, truth=6.0, forecast=2.0))
    assert online.factors("stations", date(2026, 12, 1)) == (1.0, {})
    assert online.apply(_pred(), "stations", date(2026, 12, 1))["snowfall_in"].tolist() == [10.0, 10.0]


def test_correction_is_bounded_and_never_applied_twice(monkeypatch):
    # 30 rounds of a 3x under-forecast: the old layer sent out 4x (league 2x times station 2x)
    monkeypatch.setattr(online, "our_history", lambda league, before: _history(30, truth=6.0, forecast=2.0))
    lg, st = online.factors("stations", date(2026, 12, 1))
    assert lg == online.CLIP[1]
    out = online.apply(_pred(), "stations", date(2026, 12, 1))
    assert out["snowfall_in"].max() <= 10.0 * online.CLIP[1] + 1e-9  # product clipped, known station
    assert out.loc[out.station_id == "NEW", "snowfall_in"].item() == 10.0 * lg  # no history: the league factor once


def test_station_factor_is_relative_to_the_league(monkeypatch):
    h = pd.concat([_history(30, truth=2.0, forecast=2.0, stations=("A",)),
                   _history(30, truth=0.0, forecast=0.0, stations=("DRY",))])
    monkeypatch.setattr(online, "our_history", lambda league, before: h)
    lg, st = online.factors("stations", date(2026, 12, 1))
    assert lg == 1.0 and st["A"] == 1.0 and st["DRY"] == 1.0
