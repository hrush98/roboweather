from __future__ import annotations

import argparse
import time
from datetime import date
from pathlib import Path
import json

import numpy as np
import pandas as pd

from weather_trader.config import RAW_DIR, ensure_directories
from weather_trader.config import PAPER_DIR
from weather_trader.execution.engine import PaperTradingEngine
from weather_trader.execution.fair_value import FairValueEngine
from weather_trader.execution.risk import RiskConfig, RiskManager
from weather_trader.execution.store import ExecutionStore
from weather_trader.execution.weather import WeatherFeatureService
from weather_trader.features.dataset_builder import build_default_dataset
from weather_trader.features.build_same_day_features import build_synthetic_threshold_examples
from weather_trader.forecasts.hrrr_archive import HRRRArchiveClient, enrich_dataset_with_hrrr
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
from weather_trader.models.next_day_classifier import (
    build_next_day_bucket_reports,
    build_next_day_threshold_dataset,
    build_next_day_validation_predictions,
    save_next_day_artifacts,
    train_next_day_classifier,
)
from weather_trader.stations.iem_asos_client import IEMASOSClient
from weather_trader.stations.metadata import get_station


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

    train_model = subparsers.add_parser("train-model", help="Train calibrated classifier")
    train_model.add_argument("--dataset", required=True)
    train_model.add_argument("--output", required=True)
    train_model.add_argument("--validation-year", type=int, default=2025)
    train_model.add_argument("--report-dir", required=False)
    train_model.add_argument("--require-hrrr", action="store_true")

    tune_model = subparsers.add_parser("tune-model", help="Compare same-day classifier configs on chronological validation")
    tune_model.add_argument("--dataset", required=True)
    tune_model.add_argument("--output", required=True)
    tune_model.add_argument("--validation-year", type=int, default=2025)
    tune_model.add_argument("--require-hrrr", action="store_true")

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
    paper_cycle.add_argument("--max-obs-age-minutes", type=int, default=25)

    paper_loop = subparsers.add_parser("paper-loop", help="Run repeated paper-trading cycles for live logging")
    paper_loop.add_argument("--model", required=True)
    paper_loop.add_argument("--db", default=str(PAPER_DIR / "roboweather.sqlite"))
    paper_loop.add_argument("--market-limit", type=int, default=50000)
    paper_loop.add_argument("--bankroll", type=float, default=1000.0)
    paper_loop.add_argument("--interval-seconds", type=int, default=300)
    paper_loop.add_argument("--submit-paper-orders", action="store_true")
    paper_loop.add_argument("--max-cycles", type=int, default=None)
    paper_loop.add_argument("--max-obs-age-minutes", type=int, default=25)

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
        build_dataset_command(args.start, args.end, args.all_stations)
        return
    if args.command == "train-model":
        train_model_command(args.dataset, args.output, args.validation_year, args.report_dir, args.require_hrrr)
        return
    if args.command == "tune-model":
        tune_model_command(args.dataset, args.output, args.validation_year, args.require_hrrr)
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
    if args.command == "tui":
        tui_command(args.db)
        return
    raise ValueError(f"Unhandled command: {args.command}")


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


def build_dataset_command(start: str, end: str, all_stations: bool) -> None:
    dataset = build_default_dataset(
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        initial_only=not all_stations,
    )
    output = RAW_DIR / f"dataset_{start}_{end}_{'all' if all_stations else 'initial5'}.csv"
    dataset.to_csv(output, index=False)
    print(output)


def train_model_command(
    dataset_path: str,
    output_path: str,
    validation_year: int,
    report_dir: str | None,
    require_hrrr: bool,
) -> None:
    dataset = pd.read_csv(dataset_path)
    if require_hrrr:
        dataset = dataset.loc[dataset["hrrr_remaining_max"].notna()].copy()
    artifacts = train_and_calibrate(dataset=dataset, validation_year=validation_year)
    save_artifacts(artifacts, Path(output_path))
    output = {
        "train_rows": artifacts.train_rows,
        "validation_rows": artifacts.validation_rows,
        "feature_columns": artifacts.feature_columns,
        "metrics": artifacts.metrics,
    }
    if report_dir:
        report_path = Path(report_dir)
        report_path.mkdir(parents=True, exist_ok=True)
        predictions = build_validation_predictions(
            dataset=dataset,
            model=artifacts.model,
            feature_columns=artifacts.feature_columns,
            validation_year=validation_year,
        )
        predictions.to_csv(report_path / "validation_predictions.csv", index=False)
        bucket_reports = build_bucket_reports(predictions)
        for name, frame in bucket_reports.items():
            frame.to_csv(report_path / f"bucket_{name}.csv", index=False)
        build_reliability_report(predictions).to_csv(report_path / "reliability.csv", index=False)
        build_station_report(predictions).to_csv(report_path / "station_metrics.csv", index=False)
        output["report_dir"] = str(report_path)
    print(json.dumps(output, indent=2))


def tune_model_command(dataset_path: str, output_path: str, validation_year: int, require_hrrr: bool) -> None:
    dataset = pd.read_csv(dataset_path)
    if require_hrrr:
        dataset = dataset.loc[dataset["hrrr_remaining_max"].notna()].copy()
    results = tune_model_configs(dataset=dataset, validation_year=validation_year)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(output, index=False)
    print(
        json.dumps(
            {
                "output": str(output),
                "best": results.iloc[0].to_dict() if not results.empty else None,
                "rows": int(len(results)),
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
        output["report_dir"] = str(report_path)
    print(json.dumps(output, indent=2))


def enrich_hrrr_command(
    dataset_path: str,
    output_path: str,
    max_snapshots: int | None,
    max_snapshots_per_year: int | None,
    forecast_stride_hours: int,
    sample_strategy: str,
) -> None:
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


def _bucket_label(lower_f: float | None, upper_f: float | None) -> str:
    if lower_f is not None and upper_f is not None:
        return f"{lower_f:g}-{upper_f:g}F"
    if lower_f is not None:
        return f">={lower_f:g}F"
    if upper_f is not None:
        return f"<={upper_f:g}F"
    return "unknown"


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
