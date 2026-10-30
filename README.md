# open-powder-ml

An open-source machine-learning entry in [PowderBench](https://powderbench.com), the live
mountain-snowfall forecasting benchmark. Every day, entirely on GitHub Actions, it forecasts
every open round in every in-season league and submits through the same public pull-request
contract as any other competitor:

- team `open-powder-ml`
- an AI/ML entry
- only information available before each cutoff

| League | Truth | Cutoff | Model |
|---|---|---|---|
| stations | SNOTEL / CDEC / SNOLITE / BC snow-depth change | 00 UTC D | `nh` GBM |
| observers | GHCN-D / ECCC measured new snow | 00 UTC D | `nh` GBM |
| alps | SLF + GeoSphere measured new snow | 21 UTC D−1 | `nh` GBM |
| resorts-na | North American resort reports | 00 UTC D | `nh` (observers flavour) + online bias → own bundle |
| era5 | ERA5 at Southern Hemisphere resorts | 11 UTC D−1 | `era5` GBM |
| resorts | Southern Hemisphere resort reports | 11 UTC D−1 | `resorts` GBM |

## Standing

<!-- scoreboard:start -->
_Updated 2026-10-30 01:24 UTC from PowderBench leaderboards._

### era5 — not entered yet

| # | team | Powder Accuracy | MAE | pinball | Brier₆ | rounds |
|---|---|---|---|---|---|---|
| 1 | baseline-climatology | 0.0 | 0.97 | 0.382 | 0.030 | 107 |
| 2 | baseline-zeros | -1.2 | 1.00 | 0.502 | 0.032 | 108 |
| 3 | baseline-gfs | -24.7 | 0.80 | – | – | 108 |
| 4 | baseline-openmeteo | -33.9 | 0.88 | – | – | 108 |
| 5 | baseline-persistence | -200.1 | 1.48 | – | – | 108 |
| None | southern-league-mogger | 51.7 | 0.38 | 0.152 | 0.000 | 1 |

### observers — not entered yet

| # | team | Powder Accuracy | MAE | pinball | Brier₆ | rounds |
|---|---|---|---|---|---|---|
| None | baseline-icon | 26.3 | 0.13 | – | – | 3 |
| None | baseline-gem | 25.3 | 0.13 | – | – | 3 |
| None | baseline-ukmo | 18.9 | 0.15 | – | – | 3 |
| None | baseline-consensus | 10.6 | 0.17 | – | – | 3 |
| None | baseline-ecmwf | 3.1 | 0.19 | – | – | 3 |
| None | baseline-zeros | 2.4 | 0.20 | 0.100 | 0.000 | 3 |

### resorts — **#None** of 16 (Powder Accuracy -443.3, 2 rounds, not yet ranked)

| # | team | Powder Accuracy | MAE | pinball | Brier₆ | rounds |
|---|---|---|---|---|---|---|
| 1 | baseline-icon | 3.3 | 0.26 | – | – | 5 |
| 2 | baseline-openmeteo | 0.5 | 3.91 | – | – | 106 |
| 3 | baseline-zeros | 0.4 | 4.58 | 2.287 | 0.133 | 106 |
| 4 | baseline-climatology | 0.0 | 4.68 | 2.124 | 0.132 | 102 |
| 5 | baseline-gfs | -1.1 | 3.80 | – | – | 106 |
| 6 | baseline-gem | -5.7 | 0.28 | – | – | 5 |
| None | **open-powder-ml** | -443.3 | 1.74 | 0.605 | 0.076 | 2 |

### stations — **#None** of 15 (Powder Accuracy 19.9, 3 rounds, not yet ranked)

| # | team | Powder Accuracy | MAE | pinball | Brier₆ | rounds |
|---|---|---|---|---|---|---|
| 1 | baseline-ukmo | 13.8 | 0.73 | – | – | 6 |
| 2 | baseline-consensus | 13.4 | 0.70 | – | – | 5 |
| 3 | baseline-openmeteo | 11.1 | 1.73 | – | – | 10 |
| 4 | baseline-gfs | 9.5 | 1.51 | – | – | 12 |
| 5 | baseline-icon | 4.6 | 0.82 | – | – | 6 |
| 6 | baseline-climatology | 0.0 | 1.90 | 0.672 | 0.057 | 12 |
| None | **open-powder-ml** | 19.9 | 0.76 | 0.306 | 0.028 | 3 |

<!-- scoreboard:end -->

## How it forecasts

See [docs/METHOD.md](docs/METHOD.md). In short:

1. **Inputs.** Hourly precipitation, 2 m temperature and snowfall come from up to 10 NWP
   models via [Open-Meteo](https://open-meteo.com):
   - ECMWF IFS and AIFS, GFS, ICON, GEM, UKMO, JMA, Météo-France
   - NBM and HRRR in CONUS
   Each is downscaled to the station's elevation, aggregated over station-local days D..D+2,
   and turned into snow with a temperature-dependent rain/snow split and snow-to-liquid
   ratio.
2. **Tier 1.** LightGBM quantile models (p10…p90) and a P(≥6″) classifier, trained on:
   - lead-time-matched archived forecasts (Open-Meteo Previous Runs)
   - PowderBench's own QC'd truth: HF `nakas/powderbench-truth`, ERA5, and resolved rounds

   They learn each network's truth quirks, for example the settling inside SNOTEL depth
   changes, plus a per-station bias, and are calibrated on a held-out season.
3. **Tier 0.** A widened multi-model ensemble that needs no training. It covers any gap, so
   a file always goes out.
4. **Online bias.** A shrunk per-league and per-station correction from our own resolved
   rounds.

## How it runs

All of this is in `.github/workflows/`:

| Workflow | When | What |
|---|---|---|
| `forecast.yml` | always on | Runs as a keeper: one job loops for about 5.5 h, checks every 8 min what is due, then dispatches its successor. Cron only restarts the chain. It submits a round as soon as it opens, then updates it on newer 00Z/12Z cycles. It does nothing within 45 min of a cutoff and resubmits anything that never merged. It also starts the daily backfill and training. |
| `watchdog.yml` | hourly | If a round near cutoff lacks our merged file, it dispatches the forecaster and opens an issue. |
| `backfill.yml` | 00:20 UTC | Harvests archived forecasts within the Open-Meteo free-tier budget. It is resumable and stores to the `store` release. |
| `train.yml` | 03:30 UTC | Retrains each bundle. A bundle is promoted to the `models` release only if it passes the backtest gate. |
| `scoreboard.yml` | 21:40 UTC | Renders our standing from PowderBench's leaderboards into this README. |
| `ci.yml` | push / PR | Runs lint and tests, including a contract test against PowderBench's own validator. |

GitHub cron starts 30 min to 5 h late and skips whole hours under load, which is why the
forecaster is a self-chaining keeper and decides what's due from state rather than the clock. That state is in `history/<league>/<round>.json`,
cross-checked against PowderBench main.

## Setup (to run your own copy)

1. Create the GitHub account that will own the team name. The first PR binds the name to it
   in PowderBench's `data/teams.yaml`.
2. In that account, create a **classic** personal access token with the `public_repo` scope.
3. Fork or copy this repo into that account and add the token as the repository secret
   `POWDERBENCH_TOKEN`.
4. To use a different team name, set `OPM_TEAM` in the workflow env. Names starting with
   `baseline-` and `house-` are reserved.
5. Run **Backfill** and **Train** once by hand (Actions → Run workflow), then **Forecast** with
   `dry_run`. After that, the schedules take over.

## Local use

```bash
scripts/checkout_powderbench.sh            # sparse PowderBench checkout -> work/powderbench
pip install -e '.[dev]' ./work/powderbench
opm rounds                                 # open rounds
opm run --dry-run --force -l stations      # forecast + validate, no submit
opm backfill --budget 3000                 # harvest training data
opm train --bundle nh --no-promote         # train + held-out score
pytest
```

## Data and licences

- Forecast data: [Open-Meteo](https://open-meteo.com), CC BY 4.0, free non-commercial tier.
- Truth: PowderBench and its sources; see
  [SOURCES](https://github.com/andrewnakas/powderbench/blob/main/docs/SOURCES.md).
- Code: MIT.
