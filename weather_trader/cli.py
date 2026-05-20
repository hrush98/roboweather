from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path
import json

import numpy as np
import pandas as pd

from weather_trader.config import CACHE_DIR, PROCESSED_DIR, RAW_DIR, ensure_directories
from weather_trader.config import PAPER_DIR
from weather_trader.execution.engine import PaperTradingEngine
from weather_trader.execution.fair_value import FairValueEngine
from weather_trader.execution.paper_policy import (
    DEFAULT_PROMOTED_POLICIES,
    PaperPolicyExecutionConfig,
    PaperPolicyRiskConfig,
    PaperPolicyTrader,
    adversity_profile,
)
from weather_trader.execution.contracts import PaperPolicyOrderMode
from weather_trader.execution.risk import RiskConfig, RiskManager
from weather_trader.execution.store import ExecutionStore
from weather_trader.execution.weather import WeatherFeatureService
from weather_trader.features.dataset_builder import build_default_dataset
from weather_trader.features.build_same_day_features import build_synthetic_threshold_examples
from weather_trader.forecasts.hrrr_client import HRRRClient
from weather_trader.live.scanner import LiveScanner
from weather_trader.live.next_day_scanner import NextDayScanner
from weather_trader.models.train_classifier import (
    build_bucket_reports,
    build_reliability_report,
    build_station_report,
    build_validation_predictions,
    save_artifacts,
    train_and_calibrate,
    tune_model_configs,
)
from weather_trader.models.bucket_classifier import (
    BUCKET_TUNING_CONFIGS,
    build_bucket_calibration_report,
    build_bucket_heuristic_report,
    build_bucket_validation_predictions,
    build_grouped_metrics,
    build_ladder_predictions,
    build_model_comparison_report,
    build_threshold_bucket_validation_predictions,
    save_catboost_bucket_artifacts,
    save_bucket_artifacts,
    train_catboost_bucket_classifier,
    train_bucket_classifier,
    tune_bucket_model_configs,
)
from weather_trader.models.high_regressor import (
    build_ngboost_bucket_validation_predictions,
    build_regression_bucket_validation_predictions,
    build_regression_report,
    entry_window,
    save_ngboost_artifacts,
    save_high_regression_artifacts,
    train_high_regressor,
    train_ngboost_high_regressor,
)
from weather_trader.models.next_day_classifier import (
    build_next_day_bucket_reports,
    build_next_day_threshold_dataset,
    build_next_day_validation_predictions,
    save_next_day_artifacts,
    train_next_day_classifier,
)
from weather_trader.models.diagnostics import (
    DatasetDiagnostics,
    validate_next_day_dataset,
    validate_same_day_dataset,
)
from weather_trader.research.collector import ResearchConfig, run_research_loop
from weather_trader.research.policies import ResearchPolicyEvaluator
from weather_trader.research.resolver import ResearchResolver, ResolverConfig
from weather_trader.stations.iem_asos_client import IEMASOSClient
from weather_trader.stations.metadata import get_station


PM_ACTIVE_US_STATIONS = (
    "KATL",
    "KBOS",
    "KDCA",
    "KLGA",
    "KORD",
    "KBKF",
    "KDAL",
    "KLAX",
    "KMIA",
    "KSFO",
    "KSEA",
    "KHOU",
)

DEFAULT_RESEARCH_DB = "/home/maxrush/.local/state/roboweather/research_2026-05-08_multimodel.sqlite"


def main() -> None:
    parser = argparse.ArgumentParser(description="roboweather CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    pull_obs = subparsers.add_parser("pull-obs", help="Download station observations from IEM")
    pull_obs.add_argument("--station", required=True)
    pull_obs.add_argument("--start", required=True, help="YYYY-MM-DD")
    pull_obs.add_argument("--end", required=True, help="YYYY-MM-DD")

    build_features = subparsers.add_parser("build-features", help="Build synthetic threshold features")
    build_features.add_argument("--station", required=True)
    build_features.add_argument("--start", required=True, help="YYYY-MM-DD")
    build_features.add_argument("--end", required=True, help="YYYY-MM-DD")

    build_dataset = subparsers.add_parser("build-dataset", help="Build multi-station synthetic dataset")
    build_dataset.add_argument("--start", required=True, help="YYYY-MM-DD")
    build_dataset.add_argument("--end", required=True, help="YYYY-MM-DD")
    build_dataset.add_argument("--all-stations", action="store_true")
    build_dataset.add_argument(
        "--stations",
        help="Comma-separated station IDs to build, e.g. KATL,KBOS,KSEA. Cannot be combined with --all-stations.",
    )

    train_model = subparsers.add_parser("train-model", help="Train calibrated classifier")
    train_model.add_argument("--dataset", required=True)
    train_model.add_argument("--output", required=True)
    train_model.add_argument("--validation-year", type=int, default=2025)
    train_model.add_argument("--report-dir", required=False)
    train_model.add_argument("--require-hrrr", action="store_true")
    train_model.add_argument("--temperature-metric", choices=["high", "low"], default="high")

    train_bucket_model = subparsers.add_parser("train-bucket-model", help="Train dynamic bucket candidate classifier")
    train_bucket_model.add_argument("--dataset", required=True)
    train_bucket_model.add_argument("--output", required=True)
    train_bucket_model.add_argument("--validation-year", type=int, default=2025)
    train_bucket_model.add_argument("--report-dir", required=False)
    train_bucket_model.add_argument("--require-hrrr", action="store_true")
    train_bucket_model.add_argument("--hour-local-max", type=int, default=None)
    train_bucket_model.add_argument("--bucket-config", default="current_sigmoid")
    train_bucket_model.add_argument("--temperature-metric", choices=["high", "low"], default="high")

    train_catboost_bucket_model = subparsers.add_parser("train-catboost-bucket-model", help="Train CatBoost dynamic bucket classifier")
    train_catboost_bucket_model.add_argument("--dataset", required=True)
    train_catboost_bucket_model.add_argument("--output", required=True)
    train_catboost_bucket_model.add_argument("--validation-year", type=int, default=2025)
    train_catboost_bucket_model.add_argument("--report-dir", required=False)
    train_catboost_bucket_model.add_argument("--require-hrrr", action="store_true")
    train_catboost_bucket_model.add_argument("--hour-local-max", type=int, default=None)

    compare_bucket_models = subparsers.add_parser("compare-bucket-models", help="Compare dynamic and threshold-derived bucket probabilities")
    compare_bucket_models.add_argument("--dataset", required=True)
    compare_bucket_models.add_argument("--threshold-model", required=True)
    compare_bucket_models.add_argument("--bucket-model", required=True)
    compare_bucket_models.add_argument("--regression-model", required=False)
    compare_bucket_models.add_argument("--ngboost-model", required=False)
    compare_bucket_models.add_argument("--validation-year", type=int, default=2025)
    compare_bucket_models.add_argument("--report-dir", required=True)
    compare_bucket_models.add_argument("--require-hrrr", action="store_true")

    train_regression_model = subparsers.add_parser("train-high-regression-model", help="Train final-high regression model")
    train_regression_model.add_argument("--dataset", required=True)
    train_regression_model.add_argument("--output", required=True)
    train_regression_model.add_argument("--validation-year", type=int, default=2025)
    train_regression_model.add_argument("--report-dir", required=False)
    train_regression_model.add_argument("--require-hrrr", action="store_true")

    train_ngboost_model = subparsers.add_parser("train-ngboost-model", help="Train NGBoost Normal distribution model for final high")
    train_ngboost_model.add_argument("--dataset", required=True)
    train_ngboost_model.add_argument("--output", required=True)
    train_ngboost_model.add_argument("--validation-year", type=int, default=2025)
    train_ngboost_model.add_argument("--report-dir", required=False)
    train_ngboost_model.add_argument("--require-hrrr", action="store_true")
    train_ngboost_model.add_argument("--n-estimators", type=int, default=350)
    train_ngboost_model.add_argument("--learning-rate", type=float, default=0.03)

    validate_data = subparsers.add_parser("validate-model-data", help="Run training-data diagnostics")
    validate_data.add_argument("--dataset", required=True)
    validate_data.add_argument("--kind", choices=["same-day", "next-day"], default="same-day")
    validate_data.add_argument("--validation-year", type=int, default=2025)
    validate_data.add_argument("--report-dir", required=False)
    validate_data.add_argument("--require-hrrr", action="store_true")

    tune_model = subparsers.add_parser("tune-model", help="Compare same-day classifier configs on chronological validation")
    tune_model.add_argument("--dataset", required=True)
    tune_model.add_argument("--output", required=True)
    tune_model.add_argument("--validation-year", type=int, default=2025)
    tune_model.add_argument("--require-hrrr", action="store_true")
    tune_model.add_argument("--temperature-metric", choices=["high", "low"], default="high")

    tune_bucket_model = subparsers.add_parser("tune-bucket-model", help="Compare dynamic bucket classifier configs on chronological validation")
    tune_bucket_model.add_argument("--dataset", required=True)
    tune_bucket_model.add_argument("--output", required=True)
    tune_bucket_model.add_argument("--validation-year", type=int, default=2025)
    tune_bucket_model.add_argument("--require-hrrr", action="store_true")
    tune_bucket_model.add_argument("--hour-local-max", type=int, default=None)
    tune_bucket_model.add_argument("--temperature-metric", choices=["high", "low"], default="high")

    build_next_day = subparsers.add_parser("build-next-day-dataset", help="Build synthetic next-day threshold dataset from same-day rows")
    build_next_day.add_argument("--same-day-dataset", required=True)
    build_next_day.add_argument("--output", required=True)

    train_next_day = subparsers.add_parser("train-next-day-model", help="Train next-day high threshold classifier")
    train_next_day.add_argument("--dataset", required=True)
    train_next_day.add_argument("--output", required=True)
    train_next_day.add_argument("--validation-year", type=int, default=2025)
    train_next_day.add_argument("--report-dir", required=False)

    enrich_hrrr = subparsers.add_parser("enrich-hrrr", help="Add historical HRRR archive features to a dataset")
    enrich_hrrr.add_argument("--dataset", required=True)
    enrich_hrrr.add_argument("--output", required=True)
    enrich_hrrr.add_argument("--max-snapshots", type=int, default=None)
    enrich_hrrr.add_argument("--max-snapshots-per-year", type=int, default=None)
    enrich_hrrr.add_argument("--forecast-stride-hours", type=int, default=3)
    enrich_hrrr.add_argument("--sample-strategy", choices=["head", "even"], default="head")

    hrrr_v2_cache = subparsers.add_parser("hrrr-v2-cache", help="Build/export batched historical HRRR point cache")
    hrrr_v2_cache.add_argument("--dataset", required=True)
    hrrr_v2_cache.add_argument("--cache", default=str(CACHE_DIR / "hrrr_v2.sqlite"))
    hrrr_v2_cache.add_argument("--output", default=str(PROCESSED_DIR / "dataset_hrrr_v2_enriched.csv"))
    hrrr_v2_cache.add_argument("--mode", choices=["build-cache", "export", "build-and-export", "status"], default="build-and-export")
    hrrr_v2_cache.add_argument("--stations", default="all", help="'all', 'dataset', or comma-separated station IDs")
    hrrr_v2_cache.add_argument("--max-snapshots", type=int, default=None)
    hrrr_v2_cache.add_argument("--max-snapshots-per-year", type=int, default=None)
    hrrr_v2_cache.add_argument("--forecast-stride-hours", type=int, default=3)
    hrrr_v2_cache.add_argument("--max-forecast-hour", type=int, default=18)
    hrrr_v2_cache.add_argument("--sample-strategy", choices=["head", "even"], default="even")
    hrrr_v2_cache.add_argument("--workers", type=int, default=4)
    hrrr_v2_cache.add_argument("--progress-every", type=int, default=25)

    hrrr_probe = subparsers.add_parser("hrrr-probe", help="Fetch HRRR point forecast features for one station")
    hrrr_probe.add_argument("--station", required=True)
    hrrr_probe.add_argument("--as-of", required=False, help="ISO timestamp UTC")

    scan_live = subparsers.add_parser("scan-live", help="Run live scanner against current weather markets")
    scan_live.add_argument("--model", required=True)

    scan_next_day = subparsers.add_parser("scan-next-day", help="Run next-day scanner against weather markets")
    scan_next_day.add_argument("--model", required=True)
    scan_next_day.add_argument("--target-date", required=True, help="YYYY-MM-DD")
    scan_next_day.add_argument("--market-limit", type=int, default=50000)

    paper_cycle = subparsers.add_parser("paper-cycle", help="Run one paper-trading execution cycle")
    paper_cycle.add_argument("--model", required=True)
    paper_cycle.add_argument("--db", default=str(PAPER_DIR / "roboweather.sqlite"))
    paper_cycle.add_argument("--market-limit", type=int, default=50000)
    paper_cycle.add_argument("--bankroll", type=float, default=1000.0)
    paper_cycle.add_argument("--submit-paper-orders", action="store_true")
    paper_cycle.add_argument("--max-obs-age-minutes", type=int, default=30)

    paper_loop = subparsers.add_parser("paper-loop", help="Run repeated paper-trading cycles for live logging")
    paper_loop.add_argument("--model", required=True)
    paper_loop.add_argument("--db", default=str(PAPER_DIR / "roboweather.sqlite"))
    paper_loop.add_argument("--market-limit", type=int, default=50000)
    paper_loop.add_argument("--bankroll", type=float, default=1000.0)
    paper_loop.add_argument("--interval-seconds", type=int, default=360)
    paper_loop.add_argument("--submit-paper-orders", action="store_true")
    paper_loop.add_argument("--max-cycles", type=int, default=None)
    paper_loop.add_argument("--max-obs-age-minutes", type=int, default=30)

    research_loop = subparsers.add_parser("research-loop", help="Run headless research snapshot collection and auto-resolution")
    research_loop.add_argument("--model", required=True)
    research_loop.add_argument("--threshold-model", default=None)
    research_loop.add_argument("--extra-model", dest="extra_model_paths", action="append", default=[])
    research_loop.add_argument("--db", default=str(PAPER_DIR / "roboweather.sqlite"))
    research_loop.add_argument("--market-limit", type=int, default=50000)
    research_loop.add_argument("--bankroll", type=float, default=1000.0)
    research_loop.add_argument("--interval-seconds", type=int, default=360)
    research_loop.add_argument("--max-cycles", type=int, default=None)
    research_loop.add_argument("--max-obs-age-minutes", type=int, default=30)
    research_loop.add_argument("--entry-start-local", default="10:00")
    research_loop.add_argument("--entry-end-local", default="15:00")
    research_loop.add_argument("--snapshot-start-local", default=None, help="Optional high-temp snapshot collection start, HH:MM. Defaults to entry start.")
    research_loop.add_argument("--snapshot-end-local", default=None, help="Optional high-temp snapshot collection end, HH:MM. Defaults to entry end.")
    research_loop.add_argument("--low-snapshot-start-local", default="00:00", help="Low-temp snapshot collection start, HH:MM.")
    research_loop.add_argument("--low-snapshot-end-local", default="10:00", help="Low-temp snapshot collection end, HH:MM.")
    research_loop.add_argument("--disable-policy-evaluation", action="store_true", help="Collect and resolve snapshots without materializing research policy positions.")
    research_loop.add_argument("--resolver-interval-seconds", type=int, default=3600)
    research_loop.add_argument("--resolve-after-local-hour", type=int, default=6)
    research_loop.add_argument("--enable-paper-policy-promotion", action="store_true")

    resolve_research = subparsers.add_parser("resolve-research", help="Resolve and score due research snapshots")
    resolve_research.add_argument("--db", default=str(PAPER_DIR / "roboweather.sqlite"))
    resolve_research.add_argument("--resolve-after-local-hour", type=int, default=6)

    paper_policy_cycle = subparsers.add_parser("paper-policy-cycle", help="Promote allowlisted research policies into paper execution")
    paper_policy_cycle.add_argument("--db", default=DEFAULT_RESEARCH_DB)
    paper_policy_cycle.add_argument("--market-date", default=None, help="YYYY-MM-DD. Defaults to latest research policy market date.")
    paper_policy_cycle.add_argument("--promoted-policy", action="append", default=[])
    paper_policy_cycle.add_argument("--adversity-profile", choices=["off", "mild", "stress"], default="off")
    paper_policy_cycle.add_argument("--bankroll", type=float, default=1000.0)
    paper_policy_cycle.add_argument("--fixed-fraction", type=float, default=0.02)
    paper_policy_cycle.add_argument("--max-usd-per-order", type=float, default=25.0)
    paper_policy_cycle.add_argument("--max-exposure-per-station-date", type=float, default=50.0)
    paper_policy_cycle.add_argument("--max-total-open-risk", type=float, default=150.0)
    paper_policy_cycle.add_argument("--allow-duplicate-bucket-side", action="store_true")
    _add_paper_policy_execution_args(paper_policy_cycle)

    paper_policy_loop = subparsers.add_parser("paper-policy-loop", help="Run repeated paper-policy promotion cycles")
    paper_policy_loop.add_argument("--db", default=DEFAULT_RESEARCH_DB)
    paper_policy_loop.add_argument("--market-date", default=None, help="YYYY-MM-DD. Defaults to latest research policy market date each cycle.")
    paper_policy_loop.add_argument("--promoted-policy", action="append", default=[])
    paper_policy_loop.add_argument("--adversity-profile", choices=["off", "mild", "stress"], default="off")
    paper_policy_loop.add_argument("--bankroll", type=float, default=1000.0)
    paper_policy_loop.add_argument("--fixed-fraction", type=float, default=0.02)
    paper_policy_loop.add_argument("--max-usd-per-order", type=float, default=25.0)
    paper_policy_loop.add_argument("--max-exposure-per-station-date", type=float, default=50.0)
    paper_policy_loop.add_argument("--max-total-open-risk", type=float, default=150.0)
    paper_policy_loop.add_argument("--allow-duplicate-bucket-side", action="store_true")
    _add_paper_policy_execution_args(paper_policy_loop)
    paper_policy_loop.add_argument("--interval-seconds", type=int, default=360)
    paper_policy_loop.add_argument("--max-cycles", type=int, default=None)

    tui = subparsers.add_parser("tui", help="Open Textual UI for the paper-trading database")
    tui.add_argument("--db", default=str(PAPER_DIR / "roboweather.sqlite"))

    args = parser.parse_args()
    ensure_directories()

    if args.command == "pull-obs":
        pull_observations(args.station, args.start, args.end)
        return
    if args.command == "build-features":
        build_features_command(args.station, args.start, args.end)
        return
    if args.command == "build-dataset":
        build_dataset_command(args.start, args.end, args.all_stations, args.stations)
        return
    if args.command == "train-model":
        train_model_command(args.dataset, args.output, args.validation_year, args.report_dir, args.require_hrrr, args.temperature_metric)
        return
    if args.command == "train-bucket-model":
        train_bucket_model_command(
            args.dataset,
            args.output,
            args.validation_year,
            args.report_dir,
            args.require_hrrr,
            args.hour_local_max,
            args.bucket_config,
            args.temperature_metric,
        )
        return
    if args.command == "train-catboost-bucket-model":
        train_catboost_bucket_model_command(args.dataset, args.output, args.validation_year, args.report_dir, args.require_hrrr, args.hour_local_max)
        return
    if args.command == "compare-bucket-models":
        compare_bucket_models_command(
            args.dataset,
            args.threshold_model,
            args.bucket_model,
            args.regression_model,
            args.ngboost_model,
            args.validation_year,
            args.report_dir,
            args.require_hrrr,
        )
        return
    if args.command == "train-high-regression-model":
        train_high_regression_model_command(args.dataset, args.output, args.validation_year, args.report_dir, args.require_hrrr)
        return
    if args.command == "train-ngboost-model":
        train_ngboost_model_command(
            args.dataset,
            args.output,
            args.validation_year,
            args.report_dir,
            args.require_hrrr,
            args.n_estimators,
            args.learning_rate,
        )
        return
    if args.command == "validate-model-data":
        validate_model_data_command(args.dataset, args.kind, args.validation_year, args.report_dir, args.require_hrrr)
        return
    if args.command == "tune-model":
        tune_model_command(args.dataset, args.output, args.validation_year, args.require_hrrr, args.temperature_metric)
        return
    if args.command == "tune-bucket-model":
        tune_bucket_model_command(args.dataset, args.output, args.validation_year, args.require_hrrr, args.hour_local_max, args.temperature_metric)
        return
    if args.command == "build-next-day-dataset":
        build_next_day_dataset_command(args.same_day_dataset, args.output)
        return
    if args.command == "train-next-day-model":
        train_next_day_model_command(args.dataset, args.output, args.validation_year, args.report_dir)
        return
    if args.command == "enrich-hrrr":
        enrich_hrrr_command(
            args.dataset,
            args.output,
            args.max_snapshots,
            args.max_snapshots_per_year,
            args.forecast_stride_hours,
            args.sample_strategy,
        )
        return
    if args.command == "hrrr-v2-cache":
        hrrr_v2_cache_command(
            dataset_path=args.dataset,
            cache_path=args.cache,
            output_path=args.output,
            mode=args.mode,
            stations_arg=args.stations,
            max_snapshots=args.max_snapshots,
            max_snapshots_per_year=args.max_snapshots_per_year,
            forecast_stride_hours=args.forecast_stride_hours,
            max_forecast_hour=args.max_forecast_hour,
            sample_strategy=args.sample_strategy,
            workers=args.workers,
            progress_every=args.progress_every,
        )
        return
    if args.command == "hrrr-probe":
        hrrr_probe_command(args.station, args.as_of)
        return
    if args.command == "scan-live":
        scan_live_command(args.model)
        return
    if args.command == "scan-next-day":
        scan_next_day_command(args.model, args.target_date, args.market_limit)
        return
    if args.command == "paper-cycle":
        paper_cycle_command(
            model_path=args.model,
            db_path=args.db,
            market_limit=args.market_limit,
            bankroll=args.bankroll,
            submit_paper_orders=args.submit_paper_orders,
            max_obs_age_minutes=args.max_obs_age_minutes,
        )
        return
    if args.command == "paper-loop":
        paper_loop_command(
            model_path=args.model,
            db_path=args.db,
            market_limit=args.market_limit,
            bankroll=args.bankroll,
            interval_seconds=args.interval_seconds,
            submit_paper_orders=args.submit_paper_orders,
            max_cycles=args.max_cycles,
            max_obs_age_minutes=args.max_obs_age_minutes,
        )
        return
    if args.command == "research-loop":
        research_loop_command(
            model_path=args.model,
            threshold_model_path=args.threshold_model,
            extra_model_paths=args.extra_model_paths,
            db_path=args.db,
            market_limit=args.market_limit,
            bankroll=args.bankroll,
            interval_seconds=args.interval_seconds,
            max_cycles=args.max_cycles,
            max_obs_age_minutes=args.max_obs_age_minutes,
            entry_start_local=args.entry_start_local,
            entry_end_local=args.entry_end_local,
            snapshot_start_local=args.snapshot_start_local,
            snapshot_end_local=args.snapshot_end_local,
            low_snapshot_start_local=args.low_snapshot_start_local,
            low_snapshot_end_local=args.low_snapshot_end_local,
            disable_policy_evaluation=args.disable_policy_evaluation,
            resolver_interval_seconds=args.resolver_interval_seconds,
            resolve_after_local_hour=args.resolve_after_local_hour,
            enable_paper_policy_promotion=args.enable_paper_policy_promotion,
        )
        return
    if args.command == "resolve-research":
        resolve_research_command(args.db, args.resolve_after_local_hour)
        return
    if args.command == "paper-policy-cycle":
        paper_policy_cycle_command(
            db_path=args.db,
            market_date=args.market_date,
            promoted_policies=args.promoted_policy,
            adversity_profile_name=args.adversity_profile,
            bankroll=args.bankroll,
            fixed_fraction=args.fixed_fraction,
            max_usd_per_order=args.max_usd_per_order,
            max_exposure_per_station_date=args.max_exposure_per_station_date,
            max_total_open_risk=args.max_total_open_risk,
            allow_duplicate_bucket_side=args.allow_duplicate_bucket_side,
            order_mode=args.order_mode,
            max_slippage_cents=args.max_slippage_cents,
            min_post_slippage_edge=args.min_post_slippage_edge,
            entry_intent_ttl_seconds=args.entry_intent_ttl_seconds,
            retry_cooldown_seconds=args.retry_cooldown_seconds,
            max_attempts=args.max_attempts,
        )
        return
    if args.command == "paper-policy-loop":
        paper_policy_loop_command(
            db_path=args.db,
            market_date=args.market_date,
            promoted_policies=args.promoted_policy,
            adversity_profile_name=args.adversity_profile,
            bankroll=args.bankroll,
            fixed_fraction=args.fixed_fraction,
            max_usd_per_order=args.max_usd_per_order,
            max_exposure_per_station_date=args.max_exposure_per_station_date,
            max_total_open_risk=args.max_total_open_risk,
            allow_duplicate_bucket_side=args.allow_duplicate_bucket_side,
            order_mode=args.order_mode,
            max_slippage_cents=args.max_slippage_cents,
            min_post_slippage_edge=args.min_post_slippage_edge,
            entry_intent_ttl_seconds=args.entry_intent_ttl_seconds,
            retry_cooldown_seconds=args.retry_cooldown_seconds,
            max_attempts=args.max_attempts,
            interval_seconds=args.interval_seconds,
            max_cycles=args.max_cycles,
        )
        return
    if args.command == "tui":
        tui_command(args.db)
        return
    raise ValueError(f"Unhandled command: {args.command}")


def _add_paper_policy_execution_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--order-mode", choices=[str(PaperPolicyOrderMode.FAK), str(PaperPolicyOrderMode.FOK)], default=str(PaperPolicyOrderMode.FAK))
    parser.add_argument("--max-slippage-cents", type=float, default=0.05)
    parser.add_argument("--min-post-slippage-edge", type=float, default=0.05)
    parser.add_argument("--entry-intent-ttl-seconds", type=float, default=180.0)
    parser.add_argument("--retry-cooldown-seconds", type=float, default=30.0)
    parser.add_argument("--max-attempts", type=int, default=6)


def pull_observations(station: str, start: str, end: str) -> None:
    client = IEMASOSClient()
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    observations = client.fetch_observations(station=station, start=start_date, end=end_date)
    output = RAW_DIR / f"{station.upper()}_{start}_{end}.csv"
    observations.to_csv(output, index=False)
    print(output)


def build_features_command(station: str, start: str, end: str) -> None:
    ensure_directories()
    station_meta = get_station(station)
    client = IEMASOSClient()
    observations = client.fetch_observations(
        station=station,
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
    )
    features = build_synthetic_threshold_examples(observations=observations, station=station_meta)
    output = RAW_DIR / f"{station.upper()}_{start}_{end}_features.csv"
    features.to_csv(output, index=False)
    print(output)


def build_dataset_command(start: str, end: str, all_stations: bool, stations: str | None = None) -> None:
    station_ids = _parse_station_ids(stations)
    if station_ids is not None and all_stations:
        raise ValueError("--stations cannot be combined with --all-stations")
    dataset = build_default_dataset(
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        initial_only=not all_stations,
        station_ids=station_ids,
    )
    output = RAW_DIR / f"dataset_{start}_{end}_{_dataset_station_label(all_stations, station_ids)}.csv"
    dataset.to_csv(output, index=False)
    print(output)


def _parse_station_ids(stations: str | None) -> list[str] | None:
    if stations is None:
        return None
    station_ids = [station.strip().upper() for station in stations.split(",") if station.strip()]
    if not station_ids:
        raise ValueError("--stations must include at least one station ID")
    for station_id in station_ids:
        get_station(station_id)
    return station_ids


def _dataset_station_label(all_stations: bool, station_ids: list[str] | None) -> str:
    if station_ids is None:
        return "all" if all_stations else "initial5"
    if tuple(station_ids) == PM_ACTIVE_US_STATIONS:
        return "pm_active_us12"
    return f"stations{len(station_ids)}"


def train_model_command(
    dataset_path: str,
    output_path: str,
    validation_year: int,
    report_dir: str | None,
    require_hrrr: bool,
    temperature_metric: str = "high",
) -> None:
    dataset = pd.read_csv(dataset_path)
    if require_hrrr:
        hrrr_column = "hrrr_remaining_min" if temperature_metric == "low" else "hrrr_remaining_max"
        dataset = dataset.loc[dataset[hrrr_column].notna()].copy()
    diagnostics = None if temperature_metric == "low" else validate_same_day_dataset(dataset, validation_year=validation_year)
    if diagnostics is not None and diagnostics.has_errors:
        raise ValueError(_format_diagnostic_errors(diagnostics))
    artifacts = train_and_calibrate(dataset=dataset, validation_year=validation_year, temperature_metric=temperature_metric)
    save_artifacts(artifacts, Path(output_path))
    output = {
        "train_rows": artifacts.train_rows,
        "validation_rows": artifacts.validation_rows,
        "feature_columns": artifacts.feature_columns,
        "metrics": artifacts.metrics,
        "temperature_metric": artifacts.temperature_metric,
    }
    if report_dir:
        report_path = Path(report_dir)
        report_path.mkdir(parents=True, exist_ok=True)
        predictions = build_validation_predictions(
            dataset=dataset,
            model=artifacts.model,
            feature_columns=artifacts.feature_columns,
            validation_year=validation_year,
            temperature_metric=temperature_metric,
        )
        predictions.to_csv(report_path / "validation_predictions.csv", index=False)
        bucket_reports = build_bucket_reports(predictions)
        for name, frame in bucket_reports.items():
            frame.to_csv(report_path / f"bucket_{name}.csv", index=False)
        build_reliability_report(predictions).to_csv(report_path / "reliability.csv", index=False)
        build_station_report(predictions).to_csv(report_path / "station_metrics.csv", index=False)
        if diagnostics is not None:
            _write_diagnostics(report_path, diagnostics)
        output["report_dir"] = str(report_path)
    print(json.dumps(output, indent=2))


def train_bucket_model_command(
    dataset_path: str,
    output_path: str,
    validation_year: int,
    report_dir: str | None,
    require_hrrr: bool,
    hour_local_max: int | None = None,
    bucket_config_name: str = "current_sigmoid",
    temperature_metric: str = "high",
) -> None:
    dataset = pd.read_csv(dataset_path)
    if require_hrrr:
        hrrr_column = "hrrr_remaining_min" if temperature_metric == "low" else "hrrr_remaining_max"
        dataset = dataset.loc[dataset[hrrr_column].notna()].copy()
    if hour_local_max is not None:
        dataset = dataset.loc[pd.to_numeric(dataset["hour_local"], errors="coerce") <= hour_local_max].copy()
    diagnostics = None if temperature_metric == "low" else validate_same_day_dataset(dataset, validation_year=validation_year)
    if diagnostics is not None and diagnostics.has_errors:
        raise ValueError(_format_diagnostic_errors(diagnostics))
    bucket_config = _bucket_model_config(bucket_config_name)
    artifacts = train_bucket_classifier(
        dataset=dataset,
        validation_year=validation_year,
        model_config=bucket_config,
        temperature_metric=temperature_metric,
    )
    save_bucket_artifacts(artifacts, Path(output_path))
    output = {
        "model_type": "dynamic_bucket",
        "train_rows": artifacts.train_rows,
        "validation_rows": artifacts.validation_rows,
        "feature_columns": artifacts.feature_columns,
        "ladder_config": artifacts.ladder_config.__dict__,
        "metrics": artifacts.metrics,
        "hour_local_max": hour_local_max,
        "bucket_config": bucket_config.name,
        "temperature_metric": artifacts.temperature_metric,
    }
    if report_dir:
        report_path = Path(report_dir)
        report_path.mkdir(parents=True, exist_ok=True)
        predictions = build_bucket_validation_predictions(
            dataset=dataset,
            model=artifacts.model,
            feature_columns=artifacts.feature_columns,
            validation_year=validation_year,
            ladder_config=artifacts.ladder_config,
            temperature_metric=temperature_metric,
        )
        _write_bucket_prediction_reports(report_path, predictions)
        if diagnostics is not None:
            _write_diagnostics(report_path, diagnostics)
        output["report_dir"] = str(report_path)
    print(json.dumps(output, indent=2))


def train_catboost_bucket_model_command(
    dataset_path: str,
    output_path: str,
    validation_year: int,
    report_dir: str | None,
    require_hrrr: bool,
    hour_local_max: int | None = None,
) -> None:
    dataset = pd.read_csv(dataset_path)
    if require_hrrr:
        dataset = dataset.loc[dataset["hrrr_remaining_max"].notna()].copy()
    if hour_local_max is not None:
        dataset = dataset.loc[pd.to_numeric(dataset["hour_local"], errors="coerce") <= hour_local_max].copy()
    diagnostics = validate_same_day_dataset(dataset, validation_year=validation_year)
    if diagnostics.has_errors:
        raise ValueError(_format_diagnostic_errors(diagnostics))
    artifacts = train_catboost_bucket_classifier(dataset=dataset, validation_year=validation_year)
    save_catboost_bucket_artifacts(artifacts, Path(output_path))
    output = {
        "model_type": "catboost_bucket",
        "train_rows": artifacts.train_rows,
        "validation_rows": artifacts.validation_rows,
        "feature_columns": artifacts.feature_columns,
        "ladder_config": artifacts.ladder_config.__dict__,
        "metrics": artifacts.metrics,
        "hour_local_max": hour_local_max,
    }
    if report_dir:
        report_path = Path(report_dir)
        report_path.mkdir(parents=True, exist_ok=True)
        predictions = build_bucket_validation_predictions(
            dataset=dataset,
            model=artifacts.model,
            feature_columns=artifacts.feature_columns,
            validation_year=validation_year,
            ladder_config=artifacts.ladder_config,
        )
        _write_bucket_prediction_reports(report_path, predictions)
        _write_diagnostics(report_path, diagnostics)
        output["report_dir"] = str(report_path)
    print(json.dumps(output, indent=2))


def compare_bucket_models_command(
    dataset_path: str,
    threshold_model_path: str,
    bucket_model_path: str,
    regression_model_path: str | None,
    ngboost_model_path: str | None,
    validation_year: int,
    report_dir: str,
    require_hrrr: bool,
) -> None:
    import joblib

    dataset = pd.read_csv(dataset_path)
    if require_hrrr:
        dataset = dataset.loc[dataset["hrrr_remaining_max"].notna()].copy()
    threshold_artifact = joblib.load(threshold_model_path)
    bucket_artifact = joblib.load(bucket_model_path)
    ladder_config_data = bucket_artifact.get("ladder_config") or {}
    from weather_trader.models.bucket_classifier import LadderConfig

    ladder_config = LadderConfig(**ladder_config_data)
    dynamic_predictions = build_bucket_validation_predictions(
        dataset=dataset,
        model=bucket_artifact["model"],
        feature_columns=bucket_artifact["feature_columns"],
        validation_year=validation_year,
        ladder_config=ladder_config,
    )
    threshold_predictions = build_threshold_bucket_validation_predictions(
        dataset=dataset,
        threshold_model=threshold_artifact["model"],
        threshold_feature_columns=threshold_artifact["feature_columns"],
        validation_year=validation_year,
        ladder_config=ladder_config,
    )
    dynamic_predictions["window"] = dynamic_predictions["hour_local"].map(entry_window)
    threshold_predictions["window"] = threshold_predictions["hour_local"].map(entry_window)
    comparison = build_model_comparison_report(dynamic_predictions, threshold_predictions)
    regression_predictions = None
    regression_artifact = None
    ngboost_predictions = None
    ngboost_artifact = None
    if regression_model_path:
        regression_artifact = joblib.load(regression_model_path)
        regression_predictions = build_regression_bucket_validation_predictions(
            dataset=dataset,
            model=regression_artifact["model"],
            feature_columns=regression_artifact["feature_columns"],
            residuals=regression_artifact["residuals"],
            validation_year=validation_year,
            ladder_config=ladder_config,
        )
        regression_metrics = build_grouped_metrics(regression_predictions)
        comparison = pd.concat(
            [
                comparison,
                pd.DataFrame(
                    [
                        {
                            "model": "regression_empirical_residual_bucket",
                            "grouped_log_loss": regression_metrics["grouped_log_loss"],
                            "grouped_brier_score": regression_metrics["grouped_brier_score"],
                            "top_bucket_accuracy": regression_metrics["top_bucket_accuracy"],
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
    if ngboost_model_path:
        ngboost_artifact = joblib.load(ngboost_model_path)
        ngboost_predictions = build_ngboost_bucket_validation_predictions(
            dataset=dataset,
            model=ngboost_artifact["model"],
            feature_columns=ngboost_artifact["feature_columns"],
            validation_year=validation_year,
            ladder_config=ladder_config,
        )
        ngboost_metrics = build_grouped_metrics(ngboost_predictions)
        comparison = pd.concat(
            [
                comparison,
                pd.DataFrame(
                    [
                        {
                            "model": "ngboost_normal_crps_bucket",
                            "grouped_log_loss": ngboost_metrics["grouped_log_loss"],
                            "grouped_brier_score": ngboost_metrics["grouped_brier_score"],
                            "top_bucket_accuracy": ngboost_metrics["top_bucket_accuracy"],
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )

    report_path = Path(report_dir)
    report_path.mkdir(parents=True, exist_ok=True)
    comparison.to_csv(report_path / "model_comparison.csv", index=False)
    _build_window_model_comparison(dynamic_predictions, threshold_predictions, regression_predictions, ngboost_predictions).to_csv(
        report_path / "model_comparison_by_window.csv",
        index=False,
    )
    build_ladder_predictions(dynamic_predictions).to_csv(report_path / "dynamic_bucket_ladder_predictions.csv", index=False)
    build_ladder_predictions(threshold_predictions).to_csv(report_path / "threshold_derived_ladder_predictions.csv", index=False)
    build_bucket_calibration_report(threshold_predictions).to_csv(report_path / "threshold_derived_calibration.csv", index=False)
    if regression_predictions is not None:
        regression_predictions.to_csv(report_path / "regression_bucket_validation_predictions.csv", index=False)
        build_ladder_predictions(regression_predictions).to_csv(report_path / "regression_ladder_predictions.csv", index=False)
        build_bucket_calibration_report(regression_predictions).to_csv(report_path / "regression_bucket_calibration.csv", index=False)
        build_regression_report(regression_predictions).to_csv(report_path / "regression_window_metrics.csv", index=False)
    if ngboost_predictions is not None:
        ngboost_predictions.to_csv(report_path / "ngboost_bucket_validation_predictions.csv", index=False)
        build_ladder_predictions(ngboost_predictions).to_csv(report_path / "ngboost_ladder_predictions.csv", index=False)
        build_bucket_calibration_report(ngboost_predictions).to_csv(report_path / "ngboost_bucket_calibration.csv", index=False)
        build_regression_report(ngboost_predictions).to_csv(report_path / "ngboost_window_metrics.csv", index=False)
    output = {
        "report_dir": str(report_path),
        "validation_year": validation_year,
        "threshold_model_metrics": threshold_artifact.get("metrics", {}),
        "bucket_model_metrics": bucket_artifact.get("metrics", {}),
        "regression_model_metrics": regression_artifact.get("metrics", {}) if regression_artifact else {},
        "ngboost_model_metrics": ngboost_artifact.get("metrics", {}) if ngboost_artifact else {},
        "comparison": comparison.to_dict(orient="records"),
    }
    print(json.dumps(output, indent=2))


def _build_window_model_comparison(
    dynamic_predictions: pd.DataFrame,
    threshold_predictions: pd.DataFrame,
    regression_predictions: pd.DataFrame | None,
    ngboost_predictions: pd.DataFrame | None,
) -> pd.DataFrame:
    frames: list[tuple[str, pd.DataFrame]] = [
        ("dynamic_bucket_classifier", dynamic_predictions),
        ("cumulative_threshold_derived_bucket", threshold_predictions),
    ]
    if regression_predictions is not None:
        frames.append(("regression_empirical_residual_bucket", regression_predictions))
    if ngboost_predictions is not None:
        frames.append(("ngboost_normal_crps_bucket", ngboost_predictions))

    rows = []
    for model_name, predictions in frames:
        for window, window_frame in predictions.groupby("window", observed=True):
            metrics = build_grouped_metrics(window_frame)
            rows.append(
                {
                    "window": window,
                    "model": model_name,
                    "groups": metrics["groups"],
                    "grouped_log_loss": metrics["grouped_log_loss"],
                    "grouped_brier_score": metrics["grouped_brier_score"],
                    "top_bucket_accuracy": metrics["top_bucket_accuracy"],
                }
            )
    for window, window_frame in dynamic_predictions.groupby("window", observed=True):
        metrics = build_grouped_metrics(window_frame)
        rows.extend(
            [
                {
                    "window": window,
                    "model": "uniform_over_ladder",
                    "groups": metrics["groups"],
                    "grouped_log_loss": metrics["uniform_grouped_log_loss"],
                    "grouped_brier_score": metrics["uniform_grouped_brier_score"],
                    "top_bucket_accuracy": np.nan,
                },
                {
                    "window": window,
                    "model": "bucket_containing_max_so_far",
                    "groups": metrics["groups"],
                    "grouped_log_loss": np.nan,
                    "grouped_brier_score": np.nan,
                    "top_bucket_accuracy": metrics["max_so_far_bucket_accuracy"],
                },
            ]
        )
    report = pd.DataFrame(rows)
    window_order = {"early_09_10": 0, "midday_11_12": 1, "late_13_plus": 2}
    model_order = {
        "dynamic_bucket_classifier": 0,
        "cumulative_threshold_derived_bucket": 1,
        "regression_empirical_residual_bucket": 2,
        "ngboost_normal_crps_bucket": 3,
        "uniform_over_ladder": 4,
        "bucket_containing_max_so_far": 5,
    }
    report["_window_order"] = report["window"].map(window_order)
    report["_model_order"] = report["model"].map(model_order)
    return report.sort_values(["_window_order", "_model_order"]).drop(columns=["_window_order", "_model_order"]).reset_index(drop=True)


def train_high_regression_model_command(
    dataset_path: str,
    output_path: str,
    validation_year: int,
    report_dir: str | None,
    require_hrrr: bool,
) -> None:
    dataset = pd.read_csv(dataset_path)
    if require_hrrr:
        dataset = dataset.loc[dataset["hrrr_remaining_max"].notna()].copy()
    diagnostics = validate_same_day_dataset(dataset, validation_year=validation_year)
    if diagnostics.has_errors:
        raise ValueError(_format_diagnostic_errors(diagnostics))
    artifacts = train_high_regressor(dataset=dataset, validation_year=validation_year)
    save_high_regression_artifacts(artifacts, Path(output_path))
    output = {
        "model_type": "high_regression_empirical_residual",
        "train_rows": artifacts.train_rows,
        "validation_rows": artifacts.validation_rows,
        "feature_columns": artifacts.feature_columns,
        "metrics": artifacts.metrics,
    }
    if report_dir:
        report_path = Path(report_dir)
        report_path.mkdir(parents=True, exist_ok=True)
        predictions = build_regression_bucket_validation_predictions(
            dataset=dataset,
            model=artifacts.model,
            feature_columns=artifacts.feature_columns,
            residuals=artifacts.residuals,
            validation_year=validation_year,
            ladder_config=artifacts.ladder_config,
        )
        predictions.to_csv(report_path / "regression_bucket_validation_predictions.csv", index=False)
        build_ladder_predictions(predictions).to_csv(report_path / "regression_ladder_predictions.csv", index=False)
        build_bucket_calibration_report(predictions).to_csv(report_path / "regression_bucket_calibration.csv", index=False)
        build_regression_report(predictions).to_csv(report_path / "regression_window_metrics.csv", index=False)
        _write_diagnostics(report_path, diagnostics)
        output["report_dir"] = str(report_path)
    print(json.dumps(output, indent=2))


def train_ngboost_model_command(
    dataset_path: str,
    output_path: str,
    validation_year: int,
    report_dir: str | None,
    require_hrrr: bool,
    n_estimators: int,
    learning_rate: float,
) -> None:
    dataset = pd.read_csv(dataset_path)
    if require_hrrr:
        dataset = dataset.loc[dataset["hrrr_remaining_max"].notna()].copy()
    diagnostics = validate_same_day_dataset(dataset, validation_year=validation_year)
    if diagnostics.has_errors:
        raise ValueError(_format_diagnostic_errors(diagnostics))
    artifacts = train_ngboost_high_regressor(
        dataset=dataset,
        validation_year=validation_year,
        n_estimators=n_estimators,
        learning_rate=learning_rate,
    )
    save_ngboost_artifacts(artifacts, Path(output_path))
    output = {
        "model_type": "ngboost_normal_crps",
        "train_rows": artifacts.train_rows,
        "validation_rows": artifacts.validation_rows,
        "feature_columns": artifacts.feature_columns,
        "metrics": artifacts.metrics,
    }
    if report_dir:
        report_path = Path(report_dir)
        report_path.mkdir(parents=True, exist_ok=True)
        predictions = build_ngboost_bucket_validation_predictions(
            dataset=dataset,
            model=artifacts.model,
            feature_columns=artifacts.feature_columns,
            validation_year=validation_year,
            ladder_config=artifacts.ladder_config,
        )
        predictions.to_csv(report_path / "ngboost_bucket_validation_predictions.csv", index=False)
        build_ladder_predictions(predictions).to_csv(report_path / "ngboost_ladder_predictions.csv", index=False)
        build_bucket_calibration_report(predictions).to_csv(report_path / "ngboost_bucket_calibration.csv", index=False)
        build_regression_report(predictions).to_csv(report_path / "ngboost_window_metrics.csv", index=False)
        _write_diagnostics(report_path, diagnostics)
        output["report_dir"] = str(report_path)
    print(json.dumps(output, indent=2))


def validate_model_data_command(
    dataset_path: str,
    kind: str,
    validation_year: int,
    report_dir: str | None,
    require_hrrr: bool,
) -> None:
    dataset = pd.read_csv(dataset_path)
    if require_hrrr:
        dataset = dataset.loc[dataset["hrrr_remaining_max"].notna()].copy()
    diagnostics = (
        validate_next_day_dataset(dataset, validation_year=validation_year)
        if kind == "next-day"
        else validate_same_day_dataset(dataset, validation_year=validation_year)
    )
    if report_dir:
        report_path = Path(report_dir)
        report_path.mkdir(parents=True, exist_ok=True)
        _write_diagnostics(report_path, diagnostics)
    output = {
        **diagnostics.summary,
        "kind": kind,
        "issue_count": len(diagnostics.issues),
        "error_count": sum(issue.severity == "error" for issue in diagnostics.issues),
        "warning_count": sum(issue.severity == "warning" for issue in diagnostics.issues),
    }
    print(json.dumps(output, indent=2))
    if diagnostics.issues:
        print(diagnostics.issue_frame().to_string(index=False))
    if diagnostics.has_errors:
        raise SystemExit(1)


def tune_model_command(dataset_path: str, output_path: str, validation_year: int, require_hrrr: bool, temperature_metric: str = "high") -> None:
    dataset = pd.read_csv(dataset_path)
    if require_hrrr:
        hrrr_column = "hrrr_remaining_min" if temperature_metric == "low" else "hrrr_remaining_max"
        dataset = dataset.loc[dataset[hrrr_column].notna()].copy()
    results = tune_model_configs(dataset=dataset, validation_year=validation_year, temperature_metric=temperature_metric)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(output, index=False)
    print(
        json.dumps(
            {
                "output": str(output),
                "best": results.iloc[0].to_dict() if not results.empty else None,
                "rows": int(len(results)),
                "temperature_metric": temperature_metric,
            },
            indent=2,
        )
    )


def tune_bucket_model_command(
    dataset_path: str,
    output_path: str,
    validation_year: int,
    require_hrrr: bool,
    hour_local_max: int | None = None,
    temperature_metric: str = "high",
) -> None:
    dataset = pd.read_csv(dataset_path)
    if require_hrrr:
        hrrr_column = "hrrr_remaining_min" if temperature_metric == "low" else "hrrr_remaining_max"
        dataset = dataset.loc[dataset[hrrr_column].notna()].copy()
    if hour_local_max is not None:
        dataset = dataset.loc[pd.to_numeric(dataset["hour_local"], errors="coerce") <= hour_local_max].copy()
    results = tune_bucket_model_configs(dataset=dataset, validation_year=validation_year, temperature_metric=temperature_metric)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(output, index=False)
    print(
        json.dumps(
            {
                "output": str(output),
                "best": results.iloc[0].to_dict() if not results.empty else None,
                "rows": int(len(results)),
                "hour_local_max": hour_local_max,
                "temperature_metric": temperature_metric,
            },
            indent=2,
        )
    )


def build_next_day_dataset_command(same_day_dataset_path: str, output_path: str) -> None:
    same_day_dataset = pd.read_csv(same_day_dataset_path)
    next_day = build_next_day_threshold_dataset(same_day_dataset)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    next_day.to_csv(output, index=False)
    print(
        json.dumps(
            {
                "output": str(output),
                "rows": int(len(next_day)),
                "stations": int(next_day["station"].nunique()) if not next_day.empty else 0,
                "start": str(next_day["local_date"].min()) if not next_day.empty else None,
                "end": str(next_day["local_date"].max()) if not next_day.empty else None,
            },
            indent=2,
        )
    )


def train_next_day_model_command(
    dataset_path: str,
    output_path: str,
    validation_year: int,
    report_dir: str | None,
) -> None:
    dataset = pd.read_csv(dataset_path)
    diagnostics = validate_next_day_dataset(dataset, validation_year=validation_year)
    if diagnostics.has_errors:
        raise ValueError(_format_diagnostic_errors(diagnostics))
    artifacts = train_next_day_classifier(dataset, validation_year=validation_year)
    save_next_day_artifacts(artifacts, Path(output_path))
    output = {
        "train_rows": artifacts.train_rows,
        "validation_rows": artifacts.validation_rows,
        "feature_columns": artifacts.feature_columns,
        "metrics": artifacts.metrics,
    }
    if report_dir:
        report_path = Path(report_dir)
        report_path.mkdir(parents=True, exist_ok=True)
        predictions = build_next_day_validation_predictions(
            dataset=dataset,
            model=artifacts.model,
            feature_columns=artifacts.feature_columns,
            validation_year=validation_year,
        )
        predictions.to_csv(report_path / "validation_predictions.csv", index=False)
        bucket_reports = build_next_day_bucket_reports(predictions)
        for name, frame in bucket_reports.items():
            frame.to_csv(report_path / f"bucket_{name}.csv", index=False)
        _write_diagnostics(report_path, diagnostics)
        output["report_dir"] = str(report_path)
    print(json.dumps(output, indent=2))


def _write_diagnostics(report_path: Path, diagnostics: DatasetDiagnostics) -> None:
    diagnostics.issue_frame().to_csv(report_path / "data_diagnostic_issues.csv", index=False)
    diagnostics.column_report.to_csv(report_path / "data_diagnostic_columns.csv", index=False)
    diagnostics.split_report.to_csv(report_path / "data_diagnostic_splits.csv", index=False)
    diagnostics.policy_report.to_csv(report_path / "data_diagnostic_policies.csv", index=False)


def _write_bucket_prediction_reports(report_path: Path, predictions: pd.DataFrame) -> None:
    report_predictions = predictions.copy()
    report_predictions["window"] = report_predictions["hour_local"].map(entry_window)
    report_predictions.to_csv(report_path / "candidate_validation_predictions.csv", index=False)
    build_ladder_predictions(report_predictions).to_csv(report_path / "ladder_predictions.csv", index=False)
    build_bucket_calibration_report(report_predictions).to_csv(report_path / "bucket_calibration.csv", index=False)
    build_bucket_heuristic_report(report_predictions).to_csv(report_path / "heuristic_comparison.csv", index=False)
    build_regression_report(report_predictions).to_csv(report_path / "window_metrics.csv", index=False)


def _bucket_model_config(name: str):
    configs = {config.name: config for config in BUCKET_TUNING_CONFIGS}
    try:
        return configs[name]
    except KeyError as exc:
        valid = ", ".join(sorted(configs))
        raise ValueError(f"Unknown bucket config {name!r}. Valid configs: {valid}") from exc


def _format_diagnostic_errors(diagnostics: DatasetDiagnostics) -> str:
    errors = [issue for issue in diagnostics.issues if issue.severity == "error"]
    return "Training data diagnostics failed: " + "; ".join(f"{issue.check}: {issue.message}" for issue in errors)


def enrich_hrrr_command(
    dataset_path: str,
    output_path: str,
    max_snapshots: int | None,
    max_snapshots_per_year: int | None,
    forecast_stride_hours: int,
    sample_strategy: str,
) -> None:
    from weather_trader.forecasts.hrrr_archive import HRRRArchiveClient, enrich_dataset_with_hrrr

    dataset = pd.read_csv(dataset_path)
    client = HRRRArchiveClient(forecast_stride_hours=forecast_stride_hours)
    enriched = enrich_dataset_with_hrrr(
        dataset=dataset,
        max_snapshots=max_snapshots,
        max_snapshots_per_year=max_snapshots_per_year,
        sample_strategy=sample_strategy,
        client=client,
    )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    enriched.to_csv(output, index=False)
    summary = {
        "output": str(output),
        "rows": int(len(enriched)),
        "hrrr_rows": int(enriched["hrrr_remaining_max"].notna().sum()) if "hrrr_remaining_max" in enriched else 0,
        "hrrr_snapshots": int(enriched.loc[enriched["hrrr_remaining_max"].notna(), "snapshot_time_local"].nunique()) if "hrrr_remaining_max" in enriched else 0,
    }
    print(json.dumps(summary, indent=2))


def hrrr_v2_cache_command(
    dataset_path: str,
    cache_path: str,
    output_path: str,
    mode: str,
    stations_arg: str,
    max_snapshots: int | None,
    max_snapshots_per_year: int | None,
    forecast_stride_hours: int,
    max_forecast_hour: int,
    sample_strategy: str,
    workers: int,
    progress_every: int,
) -> None:
    from weather_trader.forecasts.hrrr_v2 import HRRRV2Store, build_hrrr_v2_cache, materialize_hrrr_v2_features
    from weather_trader.stations.metadata import list_stations

    dataset = pd.read_csv(dataset_path)
    cache = Path(cache_path)

    if mode == "status":
        store = HRRRV2Store(cache)
        try:
            print(json.dumps(store.status(), indent=2))
        finally:
            store.close()
        return

    if stations_arg == "all":
        stations = list_stations(initial_only=False)
    elif stations_arg == "dataset":
        stations = [get_station(station) for station in sorted(dataset["station"].astype(str).str.upper().unique())]
    else:
        stations = [get_station(station.strip()) for station in stations_arg.split(",") if station.strip()]

    if mode in {"build-cache", "build-and-export"}:
        summary = build_hrrr_v2_cache(
            dataset=dataset,
            cache_path=cache,
            stations=stations,
            max_snapshots=max_snapshots,
            max_snapshots_per_year=max_snapshots_per_year,
            sample_strategy=sample_strategy,
            forecast_stride_hours=forecast_stride_hours,
            max_forecast_hour=max_forecast_hour,
            workers=max(1, workers),
            progress_every=progress_every,
        )
        print(json.dumps(summary, indent=2), file=sys.stderr)
        if mode == "build-cache":
            return

    enriched = materialize_hrrr_v2_features(
        dataset=dataset,
        cache_path=cache,
        max_snapshots=max_snapshots,
        max_snapshots_per_year=max_snapshots_per_year,
        sample_strategy=sample_strategy,
        forecast_stride_hours=forecast_stride_hours,
        max_forecast_hour=max_forecast_hour,
        progress_every=progress_every,
    )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    enriched.to_csv(output, index=False)
    summary = {
        "mode": mode,
        "cache": str(cache),
        "output": str(output),
        "input_rows": int(len(dataset)),
        "output_rows": int(len(enriched)),
        "hrrr_rows": int(enriched["hrrr_remaining_max"].notna().sum()) if "hrrr_remaining_max" in enriched else 0,
        "stations_extracted_per_file": [station.station for station in stations],
    }
    print(json.dumps(summary, indent=2))


def hrrr_probe_command(station: str, as_of: str | None) -> None:
    import pandas as pd

    client = HRRRClient()
    station_meta = get_station(station)
    as_of_utc = pd.Timestamp(as_of) if as_of else pd.Timestamp.utcnow()
    if as_of_utc.tzinfo is None:
        as_of_utc = as_of_utc.tz_localize("UTC")
    else:
        as_of_utc = as_of_utc.tz_convert("UTC")
    as_of_utc = as_of_utc.to_pydatetime()
    data = client.fetch_remaining_day_features(station=station_meta, as_of_utc=as_of_utc)
    print(json.dumps(data, indent=2))


def scan_live_command(model_path: str) -> None:
    scanner = LiveScanner(Path(model_path))
    frame = scanner.scan()
    if frame.empty:
        print("No parseable weather markets found.")
        return
    print(frame.to_string(index=False))


def scan_next_day_command(model_path: str, target_date: str, market_limit: int) -> None:
    scanner = NextDayScanner(Path(model_path))
    frame = scanner.scan(target_date=date.fromisoformat(target_date), limit=market_limit)
    if frame.empty:
        print("No parseable next-day weather markets found.")
        return
    display_columns = [
        "station",
        "trained_station",
        "bucket",
        "current_temp",
        "today_high_so_far",
        "prior_day_high",
        "prior_7day_high_mean",
        "yes_ask",
        "no_ask",
        "fair_yes",
        "fair_no",
        "edge_yes",
        "edge_no",
        "best_side",
        "best_edge",
        "question",
    ]
    for column in display_columns:
        if column not in frame:
            frame[column] = np.nan
    print(frame[display_columns].head(40).to_string(index=False))


def paper_cycle_command(
    model_path: str,
    db_path: str,
    market_limit: int,
    bankroll: float,
    submit_paper_orders: bool,
    max_obs_age_minutes: int,
) -> None:
    store = ExecutionStore(Path(db_path))
    try:
        engine = PaperTradingEngine(
            store=store,
            fair_value_engine=FairValueEngine(Path(model_path)),
            weather_service=WeatherFeatureService(max_obs_age_minutes=max_obs_age_minutes),
            risk_manager=RiskManager(RiskConfig(bankroll_usd=bankroll)),
        )
        result = engine.run_once(market_limit=market_limit, submit_paper_orders=submit_paper_orders)
        rows = []
        for signal in result.signals:
            best_edge = max(
                signal.edge_yes if signal.edge_yes is not None else float("-inf"),
                signal.edge_no if signal.edge_no is not None else float("-inf"),
            )
            rows.append(
                {
                    "station": signal.station,
                    "bucket": _bucket_label(signal.lower_f, signal.upper_f),
                    "fair_yes": round(signal.fair_yes, 3),
                    "yes_ask": signal.yes_ask,
                    "fair_no": round(signal.fair_no, 3),
                    "no_ask": signal.no_ask,
                    "best_edge": round(best_edge, 3),
                    "signal": str(signal.signal_side),
                    "reason": ",".join(signal.reason_codes),
                    "question": signal.question,
                }
            )
        frame = pd.DataFrame(rows)
        print(
            json.dumps(
                {
                    "db": db_path,
                    "engine_state": {
                        "discovered_markets": result.engine_state.discovered_markets,
                        "actionable_signals": result.engine_state.actionable_signals,
                        "orders_submitted": result.engine_state.orders_submitted,
                        "skipped": result.engine_state.skipped,
                        "errors": result.engine_state.errors[:10],
                    },
                },
                indent=2,
            )
        )
        if not frame.empty:
            print(
                frame.sort_values("best_edge", ascending=False)
                .head(20)
                .to_string(index=False)
            )
    finally:
        store.close()


def paper_loop_command(
    model_path: str,
    db_path: str,
    market_limit: int,
    bankroll: float,
    interval_seconds: int,
    submit_paper_orders: bool,
    max_cycles: int | None,
    max_obs_age_minutes: int,
) -> None:
    store = ExecutionStore(Path(db_path))
    try:
        engine = PaperTradingEngine(
            store=store,
            fair_value_engine=FairValueEngine(Path(model_path)),
            weather_service=WeatherFeatureService(max_obs_age_minutes=max_obs_age_minutes),
            risk_manager=RiskManager(RiskConfig(bankroll_usd=bankroll)),
        )
        cycle = 0
        while True:
            cycle += 1
            started = time.time()
            result = engine.run_once(market_limit=market_limit, submit_paper_orders=submit_paper_orders)
            print(
                json.dumps(
                    {
                        "cycle": cycle,
                        "timestamp": result.engine_state.timestamp,
                        "discovered_markets": result.engine_state.discovered_markets,
                        "actionable_signals": result.engine_state.actionable_signals,
                        "orders_submitted": result.engine_state.orders_submitted,
                        "skipped": result.engine_state.skipped,
                        "errors": result.engine_state.errors[:5],
                    },
                    indent=2,
                ),
                flush=True,
            )
            if max_cycles is not None and cycle >= max_cycles:
                break
            sleep_seconds = max(1.0, interval_seconds - (time.time() - started))
            time.sleep(sleep_seconds)
    except KeyboardInterrupt:
        print("paper-loop stopped")
    finally:
        store.close()


def research_loop_command(
    model_path: str,
    threshold_model_path: str | None,
    extra_model_paths: list[str] | None,
    db_path: str,
    market_limit: int,
    bankroll: float,
    interval_seconds: int,
    max_cycles: int | None,
    max_obs_age_minutes: int,
    entry_start_local: str,
    entry_end_local: str,
    snapshot_start_local: str | None,
    snapshot_end_local: str | None,
    low_snapshot_start_local: str,
    low_snapshot_end_local: str,
    disable_policy_evaluation: bool,
    resolver_interval_seconds: int,
    resolve_after_local_hour: int,
    enable_paper_policy_promotion: bool = False,
) -> None:
    store = ExecutionStore(Path(db_path))
    try:
        config = ResearchConfig(
            entry_start_local=_parse_hhmm(entry_start_local),
            entry_end_local=_parse_hhmm(entry_end_local),
            snapshot_start_local=_parse_hhmm(snapshot_start_local) if snapshot_start_local else None,
            snapshot_end_local=_parse_hhmm(snapshot_end_local) if snapshot_end_local else None,
            low_snapshot_start_local=_parse_hhmm(low_snapshot_start_local),
            low_snapshot_end_local=_parse_hhmm(low_snapshot_end_local),
            max_obs_age_minutes=max_obs_age_minutes,
            bankroll_usd=bankroll,
            market_limit=market_limit,
        )
        resolver = ResearchResolver(
            store=store,
            config=ResolverConfig(resolve_after_local_hour=resolve_after_local_hour),
        )
        policy_evaluator = None if disable_policy_evaluation else ResearchPolicyEvaluator(store=store)
        model_paths = [Path(model_path)]
        if threshold_model_path:
            model_paths.append(Path(threshold_model_path))
        model_paths.extend(Path(path) for path in extra_model_paths or [])
        run_research_loop(
            store=store,
            model_paths=model_paths,
            config=config,
            interval_seconds=interval_seconds,
            max_cycles=max_cycles,
            resolver=resolver,
            resolver_interval_seconds=resolver_interval_seconds,
            policy_evaluator=policy_evaluator,
            paper_policy_trader=PaperPolicyTrader(store=store) if enable_paper_policy_promotion else None,
        )
    finally:
        store.close()


def resolve_research_command(db_path: str, resolve_after_local_hour: int) -> None:
    store = ExecutionStore(Path(db_path))
    try:
        summary = ResearchResolver(
            store=store,
            config=ResolverConfig(resolve_after_local_hour=resolve_after_local_hour),
        ).resolve_due()
        print(json.dumps(summary.__dict__, indent=2))
    finally:
        store.close()


def paper_policy_cycle_command(
    db_path: str,
    market_date: str | None,
    promoted_policies: list[str],
    adversity_profile_name: str,
    bankroll: float,
    fixed_fraction: float,
    max_usd_per_order: float,
    max_exposure_per_station_date: float,
    max_total_open_risk: float,
    allow_duplicate_bucket_side: bool,
    order_mode: str,
    max_slippage_cents: float,
    min_post_slippage_edge: float,
    entry_intent_ttl_seconds: float,
    retry_cooldown_seconds: float,
    max_attempts: int,
) -> None:
    store = ExecutionStore(Path(db_path))
    try:
        result = PaperPolicyTrader(
            store=store,
            config=_paper_policy_config(
                promoted_policies=promoted_policies,
                adversity_profile_name=adversity_profile_name,
                bankroll=bankroll,
                fixed_fraction=fixed_fraction,
                max_usd_per_order=max_usd_per_order,
                max_exposure_per_station_date=max_exposure_per_station_date,
                max_total_open_risk=max_total_open_risk,
                allow_duplicate_bucket_side=allow_duplicate_bucket_side,
                order_mode=order_mode,
                max_slippage_cents=max_slippage_cents,
                min_post_slippage_edge=min_post_slippage_edge,
                entry_intent_ttl_seconds=entry_intent_ttl_seconds,
                retry_cooldown_seconds=retry_cooldown_seconds,
                max_attempts=max_attempts,
            ),
        ).run_once(market_date=market_date)
        print(json.dumps(result.__dict__, indent=2))
    finally:
        store.close()


def paper_policy_loop_command(
    db_path: str,
    market_date: str | None,
    promoted_policies: list[str],
    adversity_profile_name: str,
    bankroll: float,
    fixed_fraction: float,
    max_usd_per_order: float,
    max_exposure_per_station_date: float,
    max_total_open_risk: float,
    allow_duplicate_bucket_side: bool,
    order_mode: str,
    max_slippage_cents: float,
    min_post_slippage_edge: float,
    entry_intent_ttl_seconds: float,
    retry_cooldown_seconds: float,
    max_attempts: int,
    interval_seconds: int,
    max_cycles: int | None,
) -> None:
    store = ExecutionStore(Path(db_path))
    try:
        trader = PaperPolicyTrader(
            store=store,
            config=_paper_policy_config(
                promoted_policies=promoted_policies,
                adversity_profile_name=adversity_profile_name,
                bankroll=bankroll,
                fixed_fraction=fixed_fraction,
                max_usd_per_order=max_usd_per_order,
                max_exposure_per_station_date=max_exposure_per_station_date,
                max_total_open_risk=max_total_open_risk,
                allow_duplicate_bucket_side=allow_duplicate_bucket_side,
                order_mode=order_mode,
                max_slippage_cents=max_slippage_cents,
                min_post_slippage_edge=min_post_slippage_edge,
                entry_intent_ttl_seconds=entry_intent_ttl_seconds,
                retry_cooldown_seconds=retry_cooldown_seconds,
                max_attempts=max_attempts,
            ),
        )
        cycle = 0
        while True:
            cycle += 1
            started = time.time()
            result = trader.run_once(market_date=market_date)
            print({"cycle": cycle, **result.__dict__}, flush=True)
            if max_cycles is not None and cycle >= max_cycles:
                break
            time.sleep(max(1.0, interval_seconds - (time.time() - started)))
    except KeyboardInterrupt:
        print("paper-policy-loop stopped")
    finally:
        store.close()


def _paper_policy_config(
    *,
    promoted_policies: list[str],
    adversity_profile_name: str,
    bankroll: float,
    fixed_fraction: float,
    max_usd_per_order: float,
    max_exposure_per_station_date: float,
    max_total_open_risk: float,
    allow_duplicate_bucket_side: bool,
    order_mode: str | None = None,
    max_slippage_cents: float | None = None,
    min_post_slippage_edge: float | None = None,
    entry_intent_ttl_seconds: float | None = None,
    retry_cooldown_seconds: float | None = None,
    max_attempts: int | None = None,
) -> PaperPolicyExecutionConfig:
    base = adversity_profile(adversity_profile_name)
    return PaperPolicyExecutionConfig(
        promoted_policies=tuple(promoted_policies) if promoted_policies else DEFAULT_PROMOTED_POLICIES,
        risk=PaperPolicyRiskConfig(
            bankroll_usd=bankroll,
            fixed_fraction=fixed_fraction,
            max_usd_per_order=max_usd_per_order,
            max_exposure_per_station_date=max_exposure_per_station_date,
            max_total_open_risk=max_total_open_risk,
            allow_duplicate_bucket_side=allow_duplicate_bucket_side,
        ),
        order_mode=PaperPolicyOrderMode(order_mode) if order_mode is not None else base.order_mode,
        max_book_age_seconds=base.max_book_age_seconds,
        max_slippage_cents=base.max_slippage_cents if max_slippage_cents is None else max_slippage_cents,
        min_post_slippage_edge=base.min_post_slippage_edge if min_post_slippage_edge is None else min_post_slippage_edge,
        min_fill_usd=base.min_fill_usd,
        entry_intent_ttl_seconds=base.entry_intent_ttl_seconds if entry_intent_ttl_seconds is None else entry_intent_ttl_seconds,
        retry_cooldown_seconds=base.retry_cooldown_seconds if retry_cooldown_seconds is None else retry_cooldown_seconds,
        max_attempts=base.max_attempts if max_attempts is None else max_attempts,
        unknown_retry_grace_seconds=base.unknown_retry_grace_seconds,
        fok_miss_probability=base.fok_miss_probability,
        stale_book_probability=base.stale_book_probability,
        delayed_probability=base.delayed_probability,
        unknown_probability=base.unknown_probability,
        partial_fill_probability=base.partial_fill_probability,
        random_seed=base.random_seed,
    )


def _bucket_label(lower_f: float | None, upper_f: float | None) -> str:
    if lower_f is not None and upper_f is not None:
        return f"{lower_f:g}-{upper_f:g}F"
    if lower_f is not None:
        return f">={lower_f:g}F"
    if upper_f is not None:
        return f"<={upper_f:g}F"
    return "unknown"


def _parse_hhmm(value: str):
    from datetime import time

    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def tui_command(db_path: str) -> None:
    try:
        from weather_trader.ui.textual_app import RoboWeatherTUI
    except ModuleNotFoundError as exc:
        if exc.name == "textual":
            raise SystemExit("Textual is not installed. Install project dependencies, then rerun `python -m weather_trader.cli tui`.") from exc
        raise

    RoboWeatherTUI(Path(db_path)).run()


if __name__ == "__main__":
    main()
