from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from opm import QCOLS, features, schedule, submit, truth
from opm.contract import Round
from opm.predict import sanitize


def _round(cutoff: datetime, league="stations") -> Round:
    d = cutoff.date()
    return Round(league, d, cutoff, (d, d + timedelta(1), d + timedelta(2)), ("A", "B"), ("A",))


def _hourly(model="m1", lead=0, start="2026-01-10", days=3, precip=1.0, temp=-5.0):
    t = pd.date_range(start, periods=24 * days + 1, freq="h")
    return pd.DataFrame({"time": t, "precipitation": precip, "temperature_2m": temp, "snowfall": precip * 0.7,
                         "model": model, "lead": lead, "station_id": "A"})


def test_daily_windows_cumulate_local_days():
    d = features.daily(_hourly())
    assert set(d["date"]) >= {date(2026, 1, 10), date(2026, 1, 11), date(2026, 1, 12)}
    day = d[d["date"] == date(2026, 1, 11)].iloc[0]
    assert day["prcp"] == pytest.approx(24 / 25.4)
    # -5 C: nearly all snow at SLR 14
    assert day["slr"] == pytest.approx(24 * 14 / 25.4, rel=0.01)
    w = features.live_windows(d, date(2026, 1, 10)).set_index("horizon_h")
    assert w.loc[48, "prcp"] == pytest.approx(2 * w.loc[24, "prcp"], rel=0.05)
    assert w.loc[72, "slr"] > w.loc[48, "slr"] > w.loc[24, "slr"]


def test_archive_windows_use_matching_leads():
    # lead k carries precip k mm/h: round D's day i must come from lead i+1
    h = pd.concat([_hourly(lead=k, precip=float(k), start="2026-01-08", days=8) for k in (1, 2, 3)])
    w = features.archive_windows(features.daily(h))
    r = w[w["round_date"] == date(2026, 1, 10)].set_index("horizon_h")
    assert r.loc[24, "prcp"] == pytest.approx(24 * 1 / 25.4)
    assert r.loc[48, "prcp"] == pytest.approx(24 * (1 + 2) / 25.4)
    assert r.loc[72, "prcp"] == pytest.approx(24 * (1 + 2 + 3) / 25.4)


def test_truth_windows_require_every_day_valid():
    d = pd.DataFrame({"station_id": "A", "date": pd.date_range("2026-01-01", periods=4).date,
                      "snow24": [1.0, 2.0, 3.0, 4.0], "valid": [True, True, False, True]})
    w = truth.windows(d).set_index(["round_date", "horizon_h"])["truth_in"]
    assert w[(date(2026, 1, 1), 24)] == 1.0
    assert w[(date(2026, 1, 1), 48)] == 3.0
    assert (date(2026, 1, 1), 72) not in w.index  # day 3 invalid
    assert (date(2026, 1, 3), 24) not in w.index


def test_sanitize_makes_a_valid_schema():
    rng = np.random.default_rng(0)
    rows = []
    for sid in ("A", "B"):
        for h in (24, 48, 72):
            q = rng.uniform(0, 5, 5)  # deliberately unsorted
            rows.append({"station_id": sid, "horizon_h": h, "snowfall_in": q[2], **dict(zip(QCOLS, q)),
                         "prob_6in": 1.3})
    df = sanitize(pd.DataFrame(rows))
    q = df[list(QCOLS)].to_numpy()
    assert (np.diff(q, axis=1) >= 0).all()
    assert (df["snowfall_in"] == df["p50"]).all()
    assert df.loc[df.horizon_h != 24, "prob_6in"].isna().all()
    assert df["prob_6in"].max() <= 1
    for _, g in df.groupby("station_id"):
        assert g.sort_values("horizon_h")["p50"].is_monotonic_increasing


def test_deadline_guard():
    now = datetime.now(timezone.utc)
    with pytest.raises(submit.TooLate):
        submit.check_deadline(_round(now + timedelta(minutes=30)))
    submit.check_deadline(_round(now + timedelta(hours=3)))


def test_latest_cycle_lags_model_runs():
    t = datetime(2026, 1, 10, 6, 0, tzinfo=timezone.utc)
    assert schedule.latest_cycle(t) == "2026-01-09T12Z"
    assert schedule.latest_cycle(t + timedelta(hours=1)) == "2026-01-10T00Z"
    assert schedule.latest_cycle(t + timedelta(hours=13)) == "2026-01-10T12Z"


def test_due_initial_update_and_guard(monkeypatch, tmp_path):
    now = datetime(2026, 1, 10, 20, 0, tzinfo=timezone.utc)
    r_open = _round(datetime(2026, 1, 11, 0, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(schedule, "HISTORY", tmp_path)
    monkeypatch.setattr(schedule, "open_rounds", lambda now, leagues=None: [r_open])
    assert [a.reason for a in schedule.due(now)] == ["initial"]
    schedule.save_state(r_open, {"submissions": 1, "cycle": "2026-01-10T00Z", "updates": 0,
                                 "submitted_utc": "2026-01-10T08:00:00+00:00"})
    assert [a.reason for a in schedule.due(now)] == ["update"]  # 12Z is in by 19Z
    schedule.save_state(r_open, {"submissions": 2, "cycle": "2026-01-10T12Z", "updates": 1,
                                 "submitted_utc": "2026-01-10T19:00:00+00:00"})
    assert schedule.due(now) == []
    # merged file missing upstream -> resubmit
    assert [a.reason for a in schedule.due(now, on_upstream=lambda r: False)] == ["resubmit"]
    # inside the guard window nothing happens
    assert schedule.due(datetime(2026, 1, 10, 23, 30, tzinfo=timezone.utc)) == []


def test_stitched_history_serves_every_round_and_is_flagged():
    h = _hourly(lead=0, precip=1.0, start="2023-01-08", days=8)
    d = features.daily(h)
    d["lead"] = features.STITCHED
    w = features.archive_windows(d)
    assert (w["stitched"] == 1).all()
    r = w[w["round_date"] == date(2023, 1, 10)].set_index("horizon_h")
    assert r.loc[72, "prcp"] == pytest.approx(3 * 24 / 25.4)
    W = features.wide(w)
    assert (W["stitched"] == 1).all()
    live = features.wide(features.live_windows(features.daily(_hourly()), date(2026, 1, 10)))
    assert (live["stitched"] == 0).all()
