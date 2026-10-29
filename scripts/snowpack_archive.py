"""Build/refresh the snowpack observation archive (release `snowpack`):
daily SNWD/WTEQ/TAVG for every AWDB station in the stations league, one
parquet per water year (Oct-Sep). Run from a machine AWDB doesn't throttle:
    python scripts/snowpack_archive.py [first_water_year]
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from opm import snowpack, store  # noqa: E402
from opm.contract import stations  # noqa: E402

first = int(sys.argv[1]) if len(sys.argv) > 1 else 2019
ids = [s for s in stations("stations").index if s.endswith(snowpack.AWDB_SUFFIXES)]
out = snowpack.archive_dir()
out.mkdir(parents=True, exist_ok=True)
files = []
today = date.today()
for wy in range(first, today.year + 2):
    b, e = date(wy - 1, 10, 1), min(date(wy, 9, 30), today)
    if b > today:
        break
    o = snowpack.fetch(ids, b, e)
    p = out / f"obs_{wy}.parquet"
    o.to_parquet(p, index=False)
    files.append(p)
    print(f"water year {wy}: {len(o)} rows, {o.station_id.nunique()} stations", flush=True)
store.push(snowpack.TAG, files)
print("pushed", [f.name for f in files])
