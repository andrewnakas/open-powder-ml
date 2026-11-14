"""Live feature rows against training rows: where do they differ?

For each feature the model uses, compares the saved live rows
(history/<league>/<D>.features.parquet, featured stations, last pass) with
training rows of the same league and calendar months (featured, full-model,
lead-matched). Prints the features whose null rate or level differs, largest
first. A feature that is present in training and empty live (or the reverse)
is a train/serve mismatch.

    .venv/bin/python scripts/skew.py -l stations
"""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PKG = next(p.name for p in (ROOT / "src").iterdir() if (p / "models" / "gbm.py").exists())
NULL_GAP, LEVEL_GAP = 0.10, 0.5  # report above these (share of rows; training standard deviations)


def compare(live: pd.DataFrame, train: pd.DataFrame, feats: list[str]) -> pd.DataFrame:
    rows = []
    for h in sorted(live["horizon_h"].unique()):
        a, b = live[live["horizon_h"] == h], train[train["horizon_h"] == h]
        for f in feats:
            la = a[f] if f in a else pd.Series(np.nan, index=a.index)
            tb = b[f] if f in b else pd.Series(np.nan, index=b.index)
            null_l, null_t = float(la.isna().mean()), float(tb.isna().mean())
            sd = float(tb.std()) if tb.notna().sum() > 1 else np.nan
            z = (float(la.mean()) - float(tb.mean())) / sd if sd and sd > 0 and la.notna().any() else np.nan
            rows.append({"horizon_h": int(h), "feature": f, "null_live": round(null_l, 2),
                         "null_train": round(null_t, 2), "mean_live": la.mean(), "mean_train": tb.mean(),
                         "z": z})
    d = pd.DataFrame(rows)
    d["null_gap"] = (d["null_live"] - d["null_train"]).abs()
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-l", "--leagues", nargs="+", default=["stations"])
    args = ap.parse_args()
    dataset = importlib.import_module(f"{PKG}.dataset")
    gbm = importlib.import_module(f"{PKG}.models.gbm")
    for lg in args.leagues:
        files = sorted((ROOT / "history" / lg).glob("*.features.parquet"))
        if not files:
            print(f"{lg}: no saved live features")
            continue
        live = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        tr = dataset.training_table(lg)
        tr = tr[tr["featured"] == 1]
        for col in ("aug", "stitched"):
            if col in tr:
                tr = tr[tr[col].fillna(0) == 0]
        months = set(pd.to_datetime(live["round_date"]).dt.month)
        tr = tr[pd.to_datetime(tr["round_date"]).dt.month.isin(months)]
        feats = [f for f in gbm.feature_list(tr) if f != "stn_ratio"]
        d = compare(live, tr, feats)
        bad = d[(d["null_gap"] > NULL_GAP) | (d["z"].abs() > LEVEL_GAP)].sort_values(
            ["null_gap", "z"], ascending=False, key=lambda s: s.abs())
        print(f"\n{lg}: {len(files)} live rounds ({len(live)} rows) vs {len(tr)} training rows in months "
              f"{sorted(months)}; {len(bad)} of {len(d)} feature/horizon pairs differ")
        if len(bad):
            print(bad.drop(columns="null_gap").to_string(index=False, float_format=lambda x: f"{x:.2f}"))


if __name__ == "__main__":
    main()
