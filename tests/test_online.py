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


def test_correction_is_bounded_and_shrunk(monkeypatch):
    # 30 rounds of a 3x under-forecast: the first layer sent out 4x (league 2x times station 2x)
    monkeypatch.setattr(online, "our_history", lambda league, before: _history(30, truth=6.0, forecast=2.0))
    lg, st = online.factors("stations", date(2026, 12, 1))
    assert 1.2 < lg <= online.CLIP[1] and st == {}
    out = online.apply(_pred(), "stations", date(2026, 12, 1))
    assert out["snowfall_in"].tolist() == [10.0 * lg, 10.0 * lg]  # the league factor, once, for every station


def test_a_good_median_forecast_is_left_alone(monkeypatch):
    # nine days of an inch as forecast and one 12-inch day: truth totals twice
    # the forecast, yet no multiplier lowers the absolute error
    rows = []
    for i in range(30):
        rows.append({"station_id": "A", "round_date": date(2026, 11, 1) + timedelta(days=i), "horizon_h": 24,
                     "snowfall_in": 1.0, "truth_in": 12.0 if i % 10 == 0 else 1.0})
    h = pd.DataFrame(rows)
    assert h["truth_in"].sum() / h["snowfall_in"].sum() > 2
    monkeypatch.setattr(online, "our_history", lambda league, before: h)
    assert online.factors("stations", date(2026, 12, 1)) == (1.0, {})
