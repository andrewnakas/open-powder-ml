#!/usr/bin/env bash
# Shallow, sparse checkout of PowderBench: its code (to `pip install` the exact
# validator the auto-merge bot runs) and its public data (leagues, stations,
# open round manifests, climatology, past submissions/truth/results), without
# the multi-GB API cache. Re-running pulls the latest main.
set -euo pipefail
DIR="${1:-work/powderbench}"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch -q --depth 1 origin main
  git -C "$DIR" reset -q --hard origin/main
else
  mkdir -p "$(dirname "$DIR")"
  git clone -q --depth 1 --filter=blob:none --no-checkout https://github.com/andrewnakas/powderbench "$DIR"
  git -C "$DIR" sparse-checkout init --no-cone
  printf '%s\n' '/*' '!/site/' '/src/' '/data/' '!/data/cache/' '!/data/snowhistory/' '!/data/resortreports/' \
    > "$DIR/.git/info/sparse-checkout"
  git -C "$DIR" checkout -q main
fi
echo "powderbench @ $(git -C "$DIR" rev-parse --short HEAD) -> $DIR"
