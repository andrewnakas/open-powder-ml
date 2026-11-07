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
_Updated 2026-11-07 01:28 UTC from PowderBench leaderboards._

### alps — **#None** of 15 (Powder Accuracy 0.7, 1 rounds, not yet ranked)

| # | team | Powder Accuracy | MAE | pinball | Brier₆ | rounds |
|---|---|---|---|---|---|---|
| None | baseline-gem | 2.4 | 0.12 | – | – | 1 |
| None | **open-powder-ml** | 0.7 | 0.12 | 0.042 | 0.000 | 1 |
| None | baseline-ukmo | 0.1 | 0.12 | – | – | 1 |
| None | baseline-climatology | 0.0 | 0.12 | 0.108 | 0.001 | 1 |
| None | baseline-consensus | -2.0 | 0.13 | – | – | 1 |
| None | baseline-arome | -10.1 | 0.14 | – | – | 1 |

### era5 — **#None** of 17 (Powder Accuracy 44.4, 4 rounds, not yet ranked)

| # | team | Powder Accuracy | MAE | pinball | Brier₆ | rounds |
|---|---|---|---|---|---|---|
| 1 | house-analog | 38.8 | 0.07 | 0.028 | 0.000 | 5 |
| 2 | baseline-consensus | 27.2 | 0.08 | – | – | 7 |
| 3 | baseline-aifs | 13.8 | 0.10 | – | – | 7 |
| 4 | baseline-icon | 6.7 | 0.11 | – | – | 7 |
| 5 | baseline-ecmwf | 5.4 | 0.11 | – | – | 7 |
| 6 | baseline-climatology | 0.0 | 0.94 | 0.371 | 0.029 | 111 |
| None | **open-powder-ml** | 44.4 | 0.07 | 0.027 | 0.000 | 4 |

### observers — **#1** of 17 (Powder Accuracy 25.1, 7 rounds)

| # | team | Powder Accuracy | MAE | pinball | Brier₆ | rounds |
|---|---|---|---|---|---|---|
| 1 | **open-powder-ml** | 25.1 | 0.12 | 0.049 | 0.000 | 7 |
| 2 | baseline-icon | 14.8 | 0.14 | – | – | 10 |
| 3 | baseline-consensus | 7.5 | 0.14 | – | – | 10 |
| 4 | baseline-zeros | 5.5 | 0.17 | 0.085 | 0.000 | 10 |
| 5 | baseline-ukmo | 4.9 | 0.14 | – | – | 10 |
| 6 | baseline-wpc | 4.4 | 0.12 | 0.049 | 0.000 | 7 |

### resorts — **#14** of 17 (Powder Accuracy -3847.7, 7 rounds)

| # | team | Powder Accuracy | MAE | pinball | Brier₆ | rounds |
|---|---|---|---|---|---|---|
| 1 | baseline-icon | 23.4 | 0.15 | – | – | 10 |
| 2 | baseline-zeros | 4.1 | 4.37 | 2.185 | 0.127 | 111 |
| 3 | baseline-climatology | 0.0 | 4.47 | 2.027 | 0.126 | 107 |
| 4 | baseline-openmeteo | -80.2 | 3.76 | – | – | 111 |
| 5 | baseline-gfs | -82.2 | 3.65 | – | – | 111 |
| 6 | baseline-persistence | -113.5 | 4.00 | – | – | 111 |
| 14 | **open-powder-ml** | -3847.7 | 1.49 | 0.523 | 0.080 | 7 |

### resorts-na — **#None** of 17 (Powder Accuracy 24.3, 1 rounds, not yet ranked)

| # | team | Powder Accuracy | MAE | pinball | Brier₆ | rounds |
|---|---|---|---|---|---|---|
| None | baseline-gfs | 65.7 | 0.71 | – | – | 1 |
| None | baseline-openmeteo | 65.7 | 0.71 | – | – | 1 |
| None | house-blend | 45.5 | 1.12 | 0.469 | 0.144 | 1 |
| None | baseline-nbm | 39.7 | 1.24 | – | – | 1 |
| None | baseline-wpc | 39.6 | 1.25 | 0.529 | 0.111 | 1 |
| None | baseline-ukmo | 36.9 | 1.30 | – | – | 1 |
| None | **open-powder-ml** | 24.3 | 1.56 | 0.594 | 0.117 | 1 |

### stations — **#14** of 16 (Powder Accuracy -29.8, 10 rounds)

| # | team | Powder Accuracy | MAE | pinball | Brier₆ | rounds |
|---|---|---|---|---|---|---|
| 1 | baseline-wpc | 32.0 | 0.86 | 0.331 | 0.017 | 9 |
| 2 | baseline-consensus | 25.8 | 0.90 | – | – | 12 |
| 3 | house-blend | 25.1 | 0.99 | 0.464 | 0.031 | 10 |
| 4 | baseline-ukmo | 19.2 | 0.99 | – | – | 13 |
| 5 | baseline-ecmwf | 18.4 | 0.93 | – | – | 7 |
| 6 | baseline-icon | 13.3 | 1.06 | – | – | 13 |
| 14 | **open-powder-ml** | -29.8 | 1.92 | 0.623 | 0.026 | 10 |

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
