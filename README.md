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
_Updated 2026-11-02 01:06 UTC from PowderBench leaderboards._

### era5 — not entered yet

| # | team | Powder Accuracy | MAE | pinball | Brier₆ | rounds |
|---|---|---|---|---|---|---|
| 1 | baseline-climatology | 0.0 | 0.97 | 0.382 | 0.030 | 107 |
| 2 | baseline-zeros | -1.3 | 0.98 | 0.489 | 0.032 | 111 |
| 3 | baseline-gfs | -24.8 | 0.78 | – | – | 111 |
| 4 | baseline-openmeteo | -34.2 | 0.86 | – | – | 111 |
| 5 | baseline-persistence | -202.9 | 1.45 | – | – | 111 |
| None | southern-league-mogger | 51.7 | 0.38 | 0.152 | 0.000 | 1 |

### observers — **#None** of 16 (Powder Accuracy 13.1, 3 rounds, not yet ranked)

| # | team | Powder Accuracy | MAE | pinball | Brier₆ | rounds |
|---|---|---|---|---|---|---|
| 1 | baseline-ukmo | 18.3 | 0.17 | – | – | 6 |
| 2 | baseline-icon | 16.4 | 0.17 | – | – | 6 |
| 3 | baseline-consensus | 14.2 | 0.17 | – | – | 6 |
| 4 | baseline-gem | 7.9 | 0.17 | – | – | 6 |
| 5 | baseline-zeros | 1.5 | 0.21 | 0.108 | 0.000 | 6 |
| 6 | baseline-gfs | -9.3 | 0.20 | – | – | 6 |
| None | **open-powder-ml** | 13.1 | 0.19 | 0.075 | 0.000 | 3 |

### resorts — **#15** of 17 (Powder Accuracy -4342.8, 5 rounds)

| # | team | Powder Accuracy | MAE | pinball | Brier₆ | rounds |
|---|---|---|---|---|---|---|
| 1 | baseline-icon | 14.1 | 0.19 | – | – | 8 |
| 2 | baseline-zeros | 2.4 | 4.45 | 2.225 | 0.130 | 109 |
| 3 | baseline-climatology | 0.0 | 4.55 | 2.064 | 0.129 | 105 |
| 4 | baseline-gfs | -37.9 | 3.71 | – | – | 109 |
| 5 | baseline-openmeteo | -42.4 | 3.82 | – | – | 109 |
| 6 | baseline-persistence | -109.3 | 4.07 | – | – | 109 |
| 15 | **open-powder-ml** | -4342.8 | 1.88 | 0.658 | 0.082 | 5 |

### stations — **#1** of 16 (Powder Accuracy 30.1, 6 rounds)

| # | team | Powder Accuracy | MAE | pinball | Brier₆ | rounds |
|---|---|---|---|---|---|---|
| 1 | **open-powder-ml** | 30.1 | 0.77 | 0.299 | 0.021 | 6 |
| 2 | baseline-wpc | 26.5 | 0.77 | 0.303 | 0.019 | 6 |
| 3 | baseline-consensus | 23.1 | 0.74 | – | – | 8 |
| 4 | baseline-ukmo | 20.6 | 0.78 | – | – | 9 |
| 5 | house-blend | 20.4 | 0.88 | 0.441 | 0.034 | 6 |
| 6 | baseline-ecmwf | 15.5 | 0.84 | – | – | 5 |

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
