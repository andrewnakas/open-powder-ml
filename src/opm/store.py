"""Feature store and model registry, persisted as GitHub Release assets.

- release `store`: daily archive aggregates, one parquet per backfill unit
  (league x season span x station batch), so backfills resume across runs.
- release `models`: champion model bundles (`<name>.pkl`) + `<name>.json`
  metrics; the job that trains promotes by overwriting the asset.

Locally everything lives under work/store and work/models; `gh` syncs them
(GH_TOKEN in Actions; the repo is the current GITHUB_REPOSITORY).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pandas as pd

from . import WORK

STORE = WORK / "store"
MODELS = WORK / "models"


def _repo() -> str | None:
    return os.environ.get("OPM_REPO") or os.environ.get("GITHUB_REPOSITORY")


def _gh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def unit_name(league: str, start, end, ids: list[str]) -> str:
    h = hashlib.sha1(",".join(sorted(ids)).encode()).hexdigest()[:10]
    return f"daily__{league}__{start}__{end}__{h}.parquet"


def _ensure_release(tag: str) -> None:
    repo = _repo()
    if repo and _gh("release", "view", tag, "-R", repo).returncode != 0:
        _gh("release", "create", tag, "-R", repo, "--title", tag, "--notes",
            f"Automated {tag} assets (managed by open-powder-ml workflows).", "--prerelease")


def remote_assets(tag: str) -> set[str]:
    repo = _repo()
    if not repo:
        return set()
    r = _gh("release", "view", tag, "-R", repo, "--json", "assets", "-q", ".assets[].name")
    return set(r.stdout.split()) if r.returncode == 0 else set()


def pull(tag: str, dest: Path, pattern: str = "*") -> None:
    repo = _repo()
    if not repo:
        return
    dest.mkdir(parents=True, exist_ok=True)
    _gh("release", "download", tag, "-R", repo, "-D", str(dest), "-p", pattern, "--skip-existing")


def push(tag: str, files: list[Path]) -> None:
    repo = _repo()
    if not repo or not files:
        return
    _ensure_release(tag)
    for i in range(0, len(files), 20):
        r = _gh("release", "upload", tag, "-R", repo, "--clobber", *map(str, files[i : i + 20]))
        if r.returncode != 0:
            raise RuntimeError(f"release upload failed: {r.stderr[:300]}")


def load_daily(league: str) -> pd.DataFrame:
    files = sorted((STORE).glob(f"daily__{league}__*.parquet"))
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df.drop_duplicates(["station_id", "model", "lead", "date"], keep="last")
