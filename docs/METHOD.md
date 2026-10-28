# Method

## Targets
A PowderBench round D asks for cumulative new snow over the station-local days D, D..D+1 and
D..D+2 (24/48/72 h), plus p10–p90 and P(≥ 6″ in 24 h). Each league defines truth differently:
- **SNOTEL-family stations:** positive day-over-day snow-depth change. This is net of
  settling, so it reads lower than snowfall.
- **Observers / SLF / GeoSphere:** measured new snow, attributed to the local day with a
  per-station `day_offset` (already applied in the published truth).
- **era5:** ERA5 snowfall.
- **Resort leagues:** the resorts' own reports, which run well above both ERA5 and raw models.

The point forecast is p50, because the median minimises MAE, which is what Powder Accuracy uses.

## Features
These are computed per model *m*, per station and per horizon window:
- `prcp__m`: precipitation (in).
- `swe__m`: precipitation × snow fraction, a logistic in 2 m temperature centred on 1 °C.
- `slr__m`: snow depth from `swe` × snow-to-liquid ratio, about 10 at 0 °C, 14 at −5 °C,
  capped at 20.
- `snow__m`: the model's own snowfall (live only; the archive mostly lacks it).
- `twet__m`: precipitation-weighted temperature.

Cross-model summaries are the median, mean, min, max and SD of `slr`, the number of models,
and the fraction of models with ≥ 1″.

Static and contextual features:
- latitude, longitude and elevation
- the day-of-year cycle
- the league code
- `day_offset`
- PowderBench's climatology p50, mean and P(≥ 6″) for that station, day of year and horizon
- `stn_ratio`: the station's shrunk Σtruth / Σconsensus ratio, computed out-of-fold by
  season during training

Temperatures are Open-Meteo's values at the station's own elevation (lapse-rate downscaled),
which matters for mountain stations far above the model terrain.

## Training data without look-ahead
Open-Meteo's Previous Runs archive returns, for each valid hour, the value forecast by the run
issued *k* days earlier. Day *i* of round D uses lead *k = i + 1*, roughly the lead a forecast
made at the cutoff has. Live forecasts use the newest run available before the cutoff.

Splits are always by season. The last season, or the last 30% of rounds while only one
season exists, is held out for:
- early stopping
- quantile calibration: a per-horizon, per-level multiplier fitted so that coverage matches
  each nominal level
- the metrics that gate promotion

## Promotion gate
A retrained bundle replaces the champion only if both hold:
1. On its held-out data it beats the raw multi-model snow-physics consensus by ≥ 2 Powder
   Score points.
2. If the champion was scored on the same held-out season, the candidate is no more than 0.5
   points worse than it, and no more than 2% worse on pinball.

## Safety
- **Tree models can't extrapolate.** Rows above the training 99th percentile of consensus
  snow are blended toward the tier-0 ensemble.
- **Every file is checked before it is sent.** It must pass `powderbench validate` against
  the round's own station snapshot.
- **Horizons are cumulative,** so they are forced to be monotone.

## Honesty
- No information from after a cutoff is used.
- Submissions are never touched within 45 min of a cutoff or after it.
- The team is labelled as an ML entry.

## Terrain
Every station gets static terrain features from the Copernicus GLO-30 DEM (30 m), computed by
`scripts/terrain.py` and stored in `data/terrain.parquet`. The method follows mountainweather/mtnwx.
- **Local terrain:** relief within 10 km, slope, aspect, and topographic position (TPI) at 500 m,
  2 km and 10 km.
- **Height above the model grid:** the station's height above the mean terrain of a ~3 km cell
  (HRRR) and of a 0.25° cell (GEFS/ECMWF/GFS). Coarse models report 2 m temperature at their
  smoothed orography, so this offset explains much of their warm, rainy bias on mountain stations.

DEM © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018, provided under COPERNICUS
by the European Union and ESA.

## Snowpack state before the cutoff (stations league)
SNOTEL truth is the change in snow depth, so how much of a new fall shows up depends on the
pack underneath. The model gets NRCS AWDB's daily readings dated ≤ D−2 (the newest end-of-day
reading taken before a 00Z D cutoff):
- snow depth and SWE, and the pack density (SWE ÷ depth)
- new snow over the last 1 and 3 days
- the 3-day depth change
- average air temperature
- whether the ground is bare

## Late final pass
Besides the initial submission and updates on new 00Z/12Z cycles, one final pass runs 3 to
1.25 h before each cutoff. That's when the day's last usable runs have been published.
