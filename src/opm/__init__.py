"""open-powder-ml: an open-source ML competitor for PowderBench (powderbench.com)."""

from __future__ import annotations

import os
from pathlib import Path

__version__ = "0.1.0"

TEAM = os.environ.get("OPM_TEAM", "open-powder-ml")
UPSTREAM = "andrewnakas/powderbench"

HORIZONS = (24, 48, 72)
QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9)
QCOLS = ("p10", "p25", "p50", "p75", "p90")
POWDER_IN = 6.0

# Leagues trained as one Northern Hemisphere model (a `league` feature keeps
# their truth definitions apart). era5 and resorts get their own bundles
# (resort reports run far above both ERA5 and raw models). resorts-na has no
# truth history until its first rounds resolve, so it borrows the observers
# flavour of the NH model and learns per-resort bias online, until enough of
# its own resolved rounds exist for a `resorts-na` bundle.
NH_LEAGUES = ("stations", "observers", "alps")
OWN_BUNDLE_LEAGUES = ("era5", "resorts", "resorts-na")
PROXY_LEAGUE = {"resorts-na": "observers"}

# Non-featured network stations get only these models (quota); training adds
# rows built from just this subset so those forecasts stay in-distribution.
NETWORK_MODELS = ("ecmwf_ifs025", "gfs_seamless")

ROOT = Path(os.environ.get("OPM_ROOT", Path.cwd()))
WORK = ROOT / "work"


def pb_checkout() -> Path:
    """The local PowderBench checkout (sparse: code + data, no cache)."""
    return Path(os.environ.get("OPM_PB_DIR", WORK / "powderbench"))


def use_pb_data() -> None:
    """Point the powderbench package at our checkout's data/ before importing it."""
    os.environ.setdefault("POWDERBENCH_DATA_DIR", str(pb_checkout() / "data"))
