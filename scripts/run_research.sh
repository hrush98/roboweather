#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

usage() {
  cat <<'EOF'
Usage:
  ./scripts/run_research.sh [loop|resolve|tui|live-loop|live-tui]

Runs the headless research collector by default. It writes prediction snapshots,
auto-resolves prior station/date outcomes, and does not submit paper trades.

Live modes use the live execution ledger and live kill-switch path.

Environment overrides:
  MODEL=                # defaults depend on MARKET_SCOPE
  THRESHOLD_MODEL=      # defaults depend on MARKET_SCOPE
  EXTRA_MODELS=          # defaults depend on MARKET_SCOPE
  MARKET_SCOPE=all       # us|global|all; process default captures US and global research
  DB=                    # defaults to global or US active research DB by MARKET_SCOPE
  ALLOW_SYNCED_SQLITE=0
  MARKET_LIMIT=50000
  BANKROLL=1000
  INTERVAL_SECONDS=360          # research-loop default; live-loop defaults to 90 when unset
  MAX_CYCLES=
  MAX_OBS_AGE_MINUTES=30
  ENTRY_START_LOCAL=10:00
  ENTRY_END_LOCAL=15:00
  EVALUATE_POLICIES=1
  RESOLVER_INTERVAL_SECONDS=3600
  RESOLVE_AFTER_LOCAL_HOUR=6
  LIVE_MODE=dry-run          # set to live for real order submission
  LIVE_DB=$HOME/.local/state/roboweather/live_trading.sqlite
  LIVE_MODELS=               # optional space-separated model override list
  MAX_BOOK_AGE_SECONDS=10
  MAX_NOTIONAL_USD=          # optional legacy override for consensus/core sizes
  CONSENSUS_NOTIONAL_USD=30
  CORE_NOTIONAL_USD=25
  MAX_ENTRY_PRICE=0.50
  SKIP_ALLOWANCE_CHECK=0
  PYTHON=.venv/bin/python # auto-detected if unset

Examples:
  ./scripts/run_research.sh
  DB="$HOME/.local/state/roboweather/research_2026-05-08_multimodel.sqlite" ./scripts/run_research.sh
  MAX_CYCLES=10 ./scripts/run_research.sh loop
  ./scripts/run_research.sh resolve
  ./scripts/run_research.sh tui
  ./scripts/run_research.sh live-loop
  ./scripts/run_research.sh live-tui
EOF
}

mode="${1:-loop}"
if [[ "${mode}" == "-h" || "${mode}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ -z "${PYTHON:-}" ]]; then
  if [[ -n "${CONDA_PREFIX:-}" && -x "${CONDA_PREFIX}/bin/python" ]]; then
    PYTHON="${CONDA_PREFIX}/bin/python"
  elif [[ -x ".venv/bin/python" ]]; then
    PYTHON=".venv/bin/python"
  else
    PYTHON="python"
  fi
fi

MARKET_SCOPE="${MARKET_SCOPE:-all}"
case "${MARKET_SCOPE}" in
  global)
    DEFAULT_MODEL="data/models/dynamic_bucket_international_celsius_high_obs_2022_2025.joblib"
    DEFAULT_THRESHOLD_MODEL="data/models/mvp_international_celsius_high_obs_2022_2025.joblib"
    DEFAULT_EXTRA_MODELS="data/models/catboost_bucket_international_celsius_high_obs_2022_2025.joblib data/models/high_regression_international_celsius_high_obs_2022_2025.joblib data/models/ngboost_normal_international_celsius_high_obs_2022_2025.joblib data/models/dynamic_bucket_international_celsius_low_obs_2022_2025.joblib data/models/mvp_international_celsius_low_obs_2022_2025.joblib"
    DEFAULT_DB="$HOME/.local/state/roboweather/research_global_celsius_2026-05.sqlite"
    ;;
  us)
    DEFAULT_MODEL="data/models/dynamic_bucket_tuned_pm_active_us12_obs_2022_2025.joblib"
    DEFAULT_THRESHOLD_MODEL="data/models/mvp_pm_active_us12_obs_2022_2025.joblib"
    DEFAULT_EXTRA_MODELS="data/models/dynamic_bucket_pm_active_us12_obs_2022_2025.joblib data/models/high_regression_pm_active_us12_obs_2022_2025.joblib data/models/ngboost_normal_pm_active_us12_obs_2022_2025.joblib data/models/catboost_bucket_pm_active_us12_obs_2022_2025.joblib data/models/dynamic_bucket_hrrr_v2_obs_2022_2025.joblib data/models/dynamic_bucket_tuned_hrrr_v2_obs_2022_2025.joblib data/models/catboost_bucket_hrrr_v2_obs_2022_2025.joblib data/models/mvp_hrrr_v2_obs_2022_2025.joblib data/models/high_regression_hrrr_v2_obs_2022_2025.joblib data/models/ngboost_normal_hrrr_v2_obs_2022_2025.joblib data/models/low_dynamic_bucket_obs_2022_2025.joblib data/models/low_mvp_obs_2022_2025.joblib"
    DEFAULT_DB="$HOME/.local/state/roboweather/research_2026-05-08_multimodel.sqlite"
    ;;
  all)
    DEFAULT_MODEL="data/models/dynamic_bucket_tuned_pm_active_us12_obs_2022_2025.joblib"
    DEFAULT_THRESHOLD_MODEL="data/models/mvp_pm_active_us12_obs_2022_2025.joblib"
    DEFAULT_EXTRA_MODELS="data/models/dynamic_bucket_pm_active_us12_obs_2022_2025.joblib data/models/high_regression_pm_active_us12_obs_2022_2025.joblib data/models/ngboost_normal_pm_active_us12_obs_2022_2025.joblib data/models/catboost_bucket_pm_active_us12_obs_2022_2025.joblib data/models/dynamic_bucket_hrrr_v2_obs_2022_2025.joblib data/models/dynamic_bucket_tuned_hrrr_v2_obs_2022_2025.joblib data/models/catboost_bucket_hrrr_v2_obs_2022_2025.joblib data/models/mvp_hrrr_v2_obs_2022_2025.joblib data/models/high_regression_hrrr_v2_obs_2022_2025.joblib data/models/ngboost_normal_hrrr_v2_obs_2022_2025.joblib data/models/low_dynamic_bucket_obs_2022_2025.joblib data/models/low_mvp_obs_2022_2025.joblib data/models/dynamic_bucket_international_celsius_high_obs_2022_2025.joblib data/models/mvp_international_celsius_high_obs_2022_2025.joblib data/models/catboost_bucket_international_celsius_high_obs_2022_2025.joblib data/models/high_regression_international_celsius_high_obs_2022_2025.joblib data/models/ngboost_normal_international_celsius_high_obs_2022_2025.joblib data/models/dynamic_bucket_international_celsius_low_obs_2022_2025.joblib data/models/mvp_international_celsius_low_obs_2022_2025.joblib"
    DEFAULT_DB="$HOME/.local/state/roboweather/research_2026-05-08_multimodel.sqlite"
    ;;
  *)
    echo "Invalid MARKET_SCOPE=${MARKET_SCOPE}; expected us, global, or all." >&2
    exit 2
    ;;
esac
MODEL="${MODEL:-${DEFAULT_MODEL}}"
THRESHOLD_MODEL="${THRESHOLD_MODEL:-${DEFAULT_THRESHOLD_MODEL}}"
EXTRA_MODELS="${EXTRA_MODELS:-${DEFAULT_EXTRA_MODELS}}"
DB="${DB:-${DEFAULT_DB}}"
LIVE_DB="${LIVE_DB:-$HOME/.local/state/roboweather/live_trading.sqlite}"
MARKET_LIMIT="${MARKET_LIMIT:-50000}"
BANKROLL="${BANKROLL:-1000}"
if [[ "${mode}" == "live-loop" ]]; then
  INTERVAL_SECONDS="${INTERVAL_SECONDS:-90}"
else
  INTERVAL_SECONDS="${INTERVAL_SECONDS:-360}"
fi
MAX_CYCLES="${MAX_CYCLES:-}"
MAX_OBS_AGE_MINUTES="${MAX_OBS_AGE_MINUTES:-30}"
ENTRY_START_LOCAL="${ENTRY_START_LOCAL:-10:00}"
ENTRY_END_LOCAL="${ENTRY_END_LOCAL:-15:00}"
SNAPSHOT_START_LOCAL="${SNAPSHOT_START_LOCAL:-07:00}"
SNAPSHOT_END_LOCAL="${SNAPSHOT_END_LOCAL:-18:00}"
LOW_SNAPSHOT_START_LOCAL="${LOW_SNAPSHOT_START_LOCAL:-00:00}"
LOW_SNAPSHOT_END_LOCAL="${LOW_SNAPSHOT_END_LOCAL:-23:59}"
EVALUATE_POLICIES="${EVALUATE_POLICIES:-1}"
RESOLVER_INTERVAL_SECONDS="${RESOLVER_INTERVAL_SECONDS:-3600}"
RESOLVE_AFTER_LOCAL_HOUR="${RESOLVE_AFTER_LOCAL_HOUR:-6}"

if [[ ! -f "${MODEL}" && "${mode}" == "loop" ]]; then
  echo "Model not found: ${MODEL}" >&2
  echo "Set MODEL=/path/to/model.joblib or train a same-day model first." >&2
  exit 1
fi
if [[ ! -f "${THRESHOLD_MODEL}" && "${mode}" == "loop" ]]; then
  echo "Threshold model not found: ${THRESHOLD_MODEL}" >&2
  echo "Set THRESHOLD_MODEL=/path/to/model.joblib or leave research-loop CLI --threshold-model unset." >&2
  exit 1
fi
extra_model_paths=()
if [[ -n "${EXTRA_MODELS}" && "${mode}" == "loop" ]]; then
  read -r -a extra_model_paths <<< "${EXTRA_MODELS}"
  for extra_model_path in "${extra_model_paths[@]}"; do
    if [[ ! -f "${extra_model_path}" ]]; then
      echo "Extra model is not a file: ${extra_model_path}" >&2
      echo "EXTRA_MODELS must be a space-separated list of .joblib files on one shell line." >&2
      exit 1
    fi
  done
fi

mkdir -p "$(dirname "${DB}")" "$(dirname "${LIVE_DB}")" data/logs

db_dir="$(dirname "${DB}")"
live_db_dir="$(dirname "${LIVE_DB}")"
find_syncthing_root() {
  local path="$1"
  while [[ "${path}" != "/" && -n "${path}" ]]; do
    if [[ -e "${path}/.stfolder" ]]; then
      printf '%s\n' "${path}"
      return 0
    fi
    path="$(dirname "${path}")"
  done
  return 1
}

if [[ "${mode}" != live-* ]]; then
  if [[ ! -w "${db_dir}" ]]; then
    echo "DB directory is not writable by $(id -un): ${db_dir}" >&2
    ls -ld "${db_dir}" >&2 || true
    exit 1
  fi
  if [[ -e "${DB}" && ! -w "${DB}" ]]; then
    echo "DB file is not writable by $(id -un): ${DB}" >&2
    ls -ld "${db_dir}" "${DB}" >&2 || true
    echo "Fix ownership/permissions or set DB=/path/to/a writable sqlite file." >&2
    exit 1
  fi
fi
if [[ "${mode}" == live-* ]]; then
  if [[ ! -w "${live_db_dir}" ]]; then
    echo "Live DB directory is not writable by $(id -un): ${live_db_dir}" >&2
    ls -ld "${live_db_dir}" >&2 || true
    exit 1
  fi
  if [[ -e "${LIVE_DB}" && ! -w "${LIVE_DB}" ]]; then
    echo "Live DB file is not writable by $(id -un): ${LIVE_DB}" >&2
    ls -ld "${live_db_dir}" "${LIVE_DB}" >&2 || true
    echo "Fix ownership/permissions or set LIVE_DB=/path/to/a writable sqlite file." >&2
    exit 1
  fi
fi
db_dir_real="$(cd "${db_dir}" && pwd -P)"
live_db_dir_real="$(cd "${live_db_dir}" && pwd -P)"
if [[ "${mode}" != live-* ]] && syncthing_root="$(find_syncthing_root "${db_dir_real}")"; then
  if [[ "${ALLOW_SYNCED_SQLITE:-0}" != "1" ]]; then
    echo "Refusing to run research SQLite DB inside a Syncthing folder:" >&2
    echo "  DB=${DB}" >&2
    echo "  Syncthing root=${syncthing_root}" >&2
    echo "" >&2
    echo "SQLite needs stable local ownership and journal files during the loop." >&2
    echo "Use a non-synced DB path, for example:" >&2
    echo "  mkdir -p \"\$HOME/.local/state/roboweather\"" >&2
    echo "  DB=\"\$HOME/.local/state/roboweather/$(basename "${DB}")\" ./scripts/run_research.sh ${mode}" >&2
    echo "" >&2
    echo "To bypass this guard intentionally, set ALLOW_SYNCED_SQLITE=1." >&2
    exit 1
  fi
fi
if [[ "${mode}" == live-* ]] && syncthing_root="$(find_syncthing_root "${live_db_dir_real}")"; then
  if [[ "${ALLOW_SYNCED_SQLITE:-0}" != "1" ]]; then
    echo "Refusing to run live SQLite DB inside a Syncthing folder:" >&2
    echo "  LIVE_DB=${LIVE_DB}" >&2
    echo "  Syncthing root=${syncthing_root}" >&2
    echo "" >&2
    echo "SQLite needs stable local ownership and journal files during the live loop." >&2
    echo "Use a non-synced live DB path, for example:" >&2
    echo '  mkdir -p "$HOME/.local/state/roboweather"' >&2
    echo '  LIVE_DB="$HOME/.local/state/roboweather/live_trading.sqlite" ./scripts/run_research.sh live-loop' >&2
    echo "" >&2
    echo "To bypass this guard intentionally, set ALLOW_SYNCED_SQLITE=1." >&2
    exit 1
  fi
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
log_path="data/logs/research_${mode}_${timestamp}.log"
live_log_path="data/logs/live_${mode}_${timestamp}.log"
live_debug_log_path="${LIVE_DEBUG_LOG:-data/logs/live_debug_${mode}_${timestamp}.jsonl}"

echo "mode=${mode}"
echo "model=${MODEL}"
if [[ -n "${EXTRA_MODELS}" ]]; then
  echo "extra_models=${EXTRA_MODELS}"
fi
echo "market_scope=${MARKET_SCOPE}"
echo "db=${DB}"
echo "live_db=${LIVE_DB}"
echo "log=${log_path}"
if [[ "${mode}" == "live-loop" ]]; then
  echo "live_debug_log=${live_debug_log_path}"
fi

case "${mode}" in
  loop)
    command=(
      nice -n 10 "${PYTHON}" -u -m weather_trader.cli research-loop
      --model "${MODEL}"
      --threshold-model "${THRESHOLD_MODEL}"
      --db "${DB}"
      --market-limit "${MARKET_LIMIT}"
      --market-scope "${MARKET_SCOPE}"
      --bankroll "${BANKROLL}"
      --interval-seconds "${INTERVAL_SECONDS}"
      --max-obs-age-minutes "${MAX_OBS_AGE_MINUTES}"
      --entry-start-local "${ENTRY_START_LOCAL}"
      --entry-end-local "${ENTRY_END_LOCAL}"
      --snapshot-start-local "${SNAPSHOT_START_LOCAL}"
      --snapshot-end-local "${SNAPSHOT_END_LOCAL}"
      --low-snapshot-start-local "${LOW_SNAPSHOT_START_LOCAL}"
      --low-snapshot-end-local "${LOW_SNAPSHOT_END_LOCAL}"
      --resolver-interval-seconds "${RESOLVER_INTERVAL_SECONDS}"
      --resolve-after-local-hour "${RESOLVE_AFTER_LOCAL_HOUR}"
    )
    for extra_model_path in "${extra_model_paths[@]}"; do
      command+=(--extra-model "${extra_model_path}")
    done
    if [[ "${EVALUATE_POLICIES}" != "1" ]]; then
      command+=(--disable-policy-evaluation)
    fi
    if [[ -n "${MAX_CYCLES}" ]]; then
      command+=(--max-cycles "${MAX_CYCLES}")
    fi
    "${command[@]}" 2>&1 | tee "${log_path}"
    ;;
  resolve)
    "${PYTHON}" -m weather_trader.cli resolve-research \
      --db "${DB}" \
      --resolve-after-local-hour "${RESOLVE_AFTER_LOCAL_HOUR}" \
      --market-scope "${MARKET_SCOPE}" 2>&1 | tee "${log_path}"
    ;;
  tui)
    "${PYTHON}" -m weather_trader.cli tui --db "${DB}"
    ;;
  live-loop)
    command=(nice -n 10 "${PYTHON}" -u -m weather_trader.cli live-loop --live-db "${LIVE_DB}" --market-limit "${MARKET_LIMIT}")
    if [[ -n "${MAX_CYCLES}" ]]; then
      command+=(--max-cycles "${MAX_CYCLES}")
    fi
    if [[ -n "${INTERVAL_SECONDS}" ]]; then
      command+=(--interval-seconds "${INTERVAL_SECONDS}")
    fi
    if [[ "${MAX_OBS_AGE_MINUTES}" != "" ]]; then
      command+=(--max-obs-age-minutes "${MAX_OBS_AGE_MINUTES}")
    fi
    if [[ -n "${MAX_BOOK_AGE_SECONDS:-}" ]]; then
      command+=(--max-book-age-seconds "${MAX_BOOK_AGE_SECONDS}")
    fi
    if [[ -n "${MAX_NOTIONAL_USD:-}" ]]; then
      command+=(--max-notional-usd "${MAX_NOTIONAL_USD}")
    fi
    if [[ -n "${CONSENSUS_NOTIONAL_USD:-}" ]]; then
      command+=(--consensus-notional-usd "${CONSENSUS_NOTIONAL_USD}")
    fi
    if [[ -n "${CORE_NOTIONAL_USD:-}" ]]; then
      command+=(--core-notional-usd "${CORE_NOTIONAL_USD}")
    fi
    if [[ -n "${MAX_ENTRY_PRICE:-}" ]]; then
      command+=(--max-entry-price "${MAX_ENTRY_PRICE}")
    fi
    command+=(--debug-log "${live_debug_log_path}")
    if [[ "${LIVE_MODE:-dry-run}" != "dry-run" ]]; then
      command+=(--mode "${LIVE_MODE}")
    fi
    if [[ "${SKIP_ALLOWANCE_CHECK:-0}" == "1" || "${SKIP_ALLOWANCE_CHECK:-false}" == "true" ]]; then
      command+=(--skip-allowance-check)
    fi
    for live_model_path in ${LIVE_MODELS:-}; do
      command+=(--model "${live_model_path}")
    done
    "${command[@]}" 2>&1 | tee "${live_log_path}"
    ;;
  live-tui)
    "${PYTHON}" -m weather_trader.cli tui --db "${LIVE_DB}"
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
