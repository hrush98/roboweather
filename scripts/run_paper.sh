#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

usage() {
  cat <<'EOF'
Usage:
  ./scripts/run_paper.sh [cycle|loop|tui]

Defaults run one paper-trading cycle with station/date-level pick selection.

Environment overrides:
  MODEL=data/models/mvp_obs_corrected.joblib
  DB=data/paper/roboweather.sqlite
  MARKET_LIMIT=50000
  BANKROLL=1000
  MAX_OBS_AGE_MINUTES=30
  SUBMIT=0                # set to 1 to submit paper orders
  INTERVAL_SECONDS=360    # loop mode only
  MAX_CYCLES=             # loop mode only; blank means forever
  TIMEOUT_SECONDS=900     # cycle mode only; 0 disables timeout
  PYTHON=.venv/bin/python # auto-detected if unset

Examples:
  ./scripts/run_paper.sh
  SUBMIT=1 ./scripts/run_paper.sh cycle
  SUBMIT=1 MAX_CYCLES=6 ./scripts/run_paper.sh loop
  ./scripts/run_paper.sh tui
EOF
}

mode="${1:-cycle}"
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

MODEL="${MODEL:-data/models/mvp_obs_corrected.joblib}"
DB="${DB:-data/paper/roboweather.sqlite}"
MARKET_LIMIT="${MARKET_LIMIT:-50000}"
BANKROLL="${BANKROLL:-1000}"
MAX_OBS_AGE_MINUTES="${MAX_OBS_AGE_MINUTES:-30}"
SUBMIT="${SUBMIT:-0}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-360}"
MAX_CYCLES="${MAX_CYCLES:-}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-900}"

if [[ ! -f "${MODEL}" && "${mode}" != "tui" ]]; then
  echo "Model not found: ${MODEL}" >&2
  echo "Set MODEL=/path/to/model.joblib or train a same-day model first." >&2
  exit 1
fi

mkdir -p "$(dirname "${DB}")" data/logs

submit_args=()
if [[ "${SUBMIT}" == "1" || "${SUBMIT}" == "true" || "${SUBMIT}" == "yes" ]]; then
  submit_args+=(--submit-paper-orders)
fi

common_args=(
  --model "${MODEL}"
  --db "${DB}"
  --market-limit "${MARKET_LIMIT}"
  --bankroll "${BANKROLL}"
  --max-obs-age-minutes "${MAX_OBS_AGE_MINUTES}"
  "${submit_args[@]}"
)

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
log_path="data/logs/paper_${mode}_${timestamp}.log"

echo "mode=${mode}"
echo "model=${MODEL}"
echo "db=${DB}"
echo "log=${log_path}"
echo "submit_paper_orders=${SUBMIT}"

case "${mode}" in
  cycle)
    command=(nice -n 10 "${PYTHON}" -u -m weather_trader.cli paper-cycle "${common_args[@]}")
    if [[ "${TIMEOUT_SECONDS}" != "0" ]] && command -v timeout >/dev/null 2>&1; then
      command=(timeout "${TIMEOUT_SECONDS}" "${command[@]}")
    fi
    "${command[@]}" 2>&1 | tee "${log_path}"
    ;;
  loop)
    command=(nice -n 10 "${PYTHON}" -u -m weather_trader.cli paper-loop "${common_args[@]}" --interval-seconds "${INTERVAL_SECONDS}")
    if [[ -n "${MAX_CYCLES}" ]]; then
      command+=(--max-cycles "${MAX_CYCLES}")
    fi
    "${command[@]}" 2>&1 | tee "${log_path}"
    ;;
  tui)
    "${PYTHON}" -m weather_trader.cli tui --db "${DB}"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
