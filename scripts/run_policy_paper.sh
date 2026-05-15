#!/usr/bin/env bash
set -euo pipefail

DB="${DB:-/home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-360}"

python -m weather_trader.cli paper-policy-loop \
  --db "$DB" \
  --interval-seconds "$INTERVAL_SECONDS" \
  "$@"
