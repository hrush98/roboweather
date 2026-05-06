#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

usage() {
  cat <<'EOF'
Usage:
  ./scripts/run_research.sh [loop|resolve|tui]

Runs the headless research collector by default. It writes prediction snapshots,
auto-resolves prior station/date outcomes, and does not submit paper trades.

Environment overrides:
  MODEL=data/models/dynamic_bucket_obs_2022_2025.joblib
  THRESHOLD_MODEL=data/models/mvp_obs_corrected.joblib
  DB=data/paper/roboweather.sqlite
  MARKET_LIMIT=50000
  BANKROLL=1000
  INTERVAL_SECONDS=360
  MAX_CYCLES=
  MAX_OBS_AGE_MINUTES=30
  ENTRY_START_LOCAL=10:00
  ENTRY_END_LOCAL=15:00
  RESOLVER_INTERVAL_SECONDS=3600
  RESOLVE_AFTER_LOCAL_HOUR=6
  PYTHON=.venv/bin/python # auto-detected if unset

Examples:
  ./scripts/run_research.sh
  MAX_CYCLES=10 ./scripts/run_research.sh loop
  ./scripts/run_research.sh resolve
  ./scripts/run_research.sh tui
EOF
}

mode="${1:-loop}"
if [[ "${mode}" == "-h" || "${mode}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ -z "${PYTHON:-}" ]]; then
  if [[ -x ".venv/bin/python" ]]; then
    PYTHON=".venv/bin/python"
  else
    PYTHON="python"
  fi
fi

MODEL="${MODEL:-data/models/dynamic_bucket_obs_2022_2025.joblib}"
THRESHOLD_MODEL="${THRESHOLD_MODEL:-data/models/mvp_obs_corrected.joblib}"
DB="${DB:-data/paper/roboweather.sqlite}"
MARKET_LIMIT="${MARKET_LIMIT:-50000}"
BANKROLL="${BANKROLL:-1000}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-360}"
MAX_CYCLES="${MAX_CYCLES:-}"
MAX_OBS_AGE_MINUTES="${MAX_OBS_AGE_MINUTES:-30}"
ENTRY_START_LOCAL="${ENTRY_START_LOCAL:-10:00}"
ENTRY_END_LOCAL="${ENTRY_END_LOCAL:-15:00}"
RESOLVER_INTERVAL_SECONDS="${RESOLVER_INTERVAL_SECONDS:-3600}"
RESOLVE_AFTER_LOCAL_HOUR="${RESOLVE_AFTER_LOCAL_HOUR:-6}"

if [[ ! -f "${MODEL}" && "${mode}" == "loop" ]]; then
  echo "Model not found: ${MODEL}" >&2
  echo "Set MODEL=/path/to/model.joblib or train a same-day model first." >&2
  exit 1
fi

mkdir -p "$(dirname "${DB}")" data/logs

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
log_path="data/logs/research_${mode}_${timestamp}.log"

echo "mode=${mode}"
echo "model=${MODEL}"
echo "db=${DB}"
echo "log=${log_path}"

case "${mode}" in
  loop)
    command=(
      nice -n 10 "${PYTHON}" -u -m weather_trader.cli research-loop
      --model "${MODEL}"
      --threshold-model "${THRESHOLD_MODEL}"
      --db "${DB}"
      --market-limit "${MARKET_LIMIT}"
      --bankroll "${BANKROLL}"
      --interval-seconds "${INTERVAL_SECONDS}"
      --max-obs-age-minutes "${MAX_OBS_AGE_MINUTES}"
      --entry-start-local "${ENTRY_START_LOCAL}"
      --entry-end-local "${ENTRY_END_LOCAL}"
      --resolver-interval-seconds "${RESOLVER_INTERVAL_SECONDS}"
      --resolve-after-local-hour "${RESOLVE_AFTER_LOCAL_HOUR}"
    )
    if [[ -n "${MAX_CYCLES}" ]]; then
      command+=(--max-cycles "${MAX_CYCLES}")
    fi
    "${command[@]}" 2>&1 | tee "${log_path}"
    ;;
  resolve)
    "${PYTHON}" -m weather_trader.cli resolve-research \
      --db "${DB}" \
      --resolve-after-local-hour "${RESOLVE_AFTER_LOCAL_HOUR}" 2>&1 | tee "${log_path}"
    ;;
  tui)
    "${PYTHON}" -m weather_trader.cli tui --db "${DB}"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
