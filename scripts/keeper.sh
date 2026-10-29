#!/usr/bin/env bash
# The always-on loop behind forecast.yml. GitHub's cron drops runs under load
# (observed: hours with no firing), so instead of relying on it, one job stays
# alive for ~5.5 h, checks every few minutes what is due, and finally
# dispatches its own successor. Cron only restarts the chain if it breaks.
#
# Each pass: refresh the PowderBench checkout, forecast/submit what's due,
# commit history; once a day kick off backfill (after 00:20 UTC, when the
# Open-Meteo quota resets) and training (after 03:00 UTC).
set -uo pipefail
LOOP_MIN="${LOOP_MINUTES:-330}"
SLEEP_S="${SLEEP_SECONDS:-480}"
end=$(( $(date +%s) + LOOP_MIN * 60 ))

commit_history() {
  git add history
  git diff --cached --quiet && return 0
  git commit -qm "history: $(date -u +%Y-%m-%dT%H:%MZ)"
  for i in 1 2 3 4 5; do git pull -q --rebase && git push -q && return 0; sleep $((i * 5)); done
  echo "::warning::could not push history"
}

ran_today() {  # ran_today <workflow file> <earliest UTC hour>
  local today; today=$(date -u +%Y-%m-%d)
  [ "$(date -u +%H)" -lt "$2" ] && return 0  # too early: treat as done
  [ -n "$(gh run list -w "$1" --created ">=${today}" --json databaseId -q '.[0].databaseId' 2>/dev/null)" ]
}

while :; do
  echo "::group::pass $(date -u +%H:%MZ)"
  scripts/checkout_powderbench.sh work/powderbench >/dev/null
  if opm due | tee due.txt | grep -qv -e "nothing due" -e "^cycle"; then
    python -c "from opm import store; store.pull('models', store.MODELS, '*.pkl')" 2>/dev/null || echo "no trained models: tier 0"
    opm run ${OPM_ARGS:-} || echo "::warning::opm run reported failures"
    commit_history
  fi
  ran_today backfill.yml 0 || { [ "$(date -u +%H%M)" -ge 0020 ] && gh workflow run backfill.yml && echo "dispatched backfill"; }
  ran_today train.yml 3 || { gh workflow run train.yml && echo "dispatched train"; }
  echo "::endgroup::"
  [ $(( $(date +%s) + SLEEP_S )) -ge "$end" ] && break
  sleep "$SLEEP_S"
done

if [ "${CHAIN:-true}" = "true" ]; then
  gh workflow run forecast.yml && echo "dispatched successor"
fi
