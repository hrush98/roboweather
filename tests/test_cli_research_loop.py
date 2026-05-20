from __future__ import annotations

import sys

from weather_trader import cli
from weather_trader.execution.contracts import EngineState, utc_now_iso
from weather_trader.research import collector as research_collector
from weather_trader.research.collector import ResearchCycleResult


def test_research_loop_accepts_repeatable_extra_models(monkeypatch) -> None:
    captured = {}

    def fake_research_loop_command(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(cli, "ensure_directories", lambda: None)
    monkeypatch.setattr(cli, "research_loop_command", fake_research_loop_command)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "roboweather",
            "research-loop",
            "--model",
            "data/models/dynamic_bucket_obs_2022_2025.joblib",
            "--threshold-model",
            "data/models/mvp_obs_corrected.joblib",
            "--extra-model",
            "data/models/high_regression_obs_2022_2025.joblib",
            "--extra-model",
            "data/models/ngboost_normal_obs_2022_2025.joblib",
        ],
    )

    cli.main()

    assert captured["extra_model_paths"] == [
        "data/models/high_regression_obs_2022_2025.joblib",
        "data/models/ngboost_normal_obs_2022_2025.joblib",
    ]


def test_research_loop_accepts_paper_policy_promotion_flag(monkeypatch) -> None:
    captured = {}

    def fake_research_loop_command(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(cli, "ensure_directories", lambda: None)
    monkeypatch.setattr(cli, "research_loop_command", fake_research_loop_command)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "roboweather",
            "research-loop",
            "--model",
            "data/models/dynamic_bucket_obs_2022_2025.joblib",
            "--enable-paper-policy-promotion",
        ],
    )

    cli.main()

    assert captured["enable_paper_policy_promotion"] is True



def test_research_loop_accepts_snapshot_collection_options(monkeypatch) -> None:
    captured = {}

    def fake_research_loop_command(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(cli, "ensure_directories", lambda: None)
    monkeypatch.setattr(cli, "research_loop_command", fake_research_loop_command)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "roboweather",
            "research-loop",
            "--model",
            "data/models/dynamic_bucket_obs_2022_2025.joblib",
            "--snapshot-start-local",
            "07:00",
            "--snapshot-end-local",
            "18:00",
            "--low-snapshot-start-local",
            "00:00",
            "--low-snapshot-end-local",
            "23:59",
            "--disable-policy-evaluation",
        ],
    )

    cli.main()

    assert captured["snapshot_start_local"] == "07:00"
    assert captured["snapshot_end_local"] == "18:00"
    assert captured["low_snapshot_start_local"] == "00:00"
    assert captured["low_snapshot_end_local"] == "23:59"
    assert captured["disable_policy_evaluation"] is True

def test_paper_policy_cycle_accepts_execution_options(monkeypatch) -> None:
    captured = {}

    def fake_paper_policy_cycle_command(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(cli, "ensure_directories", lambda: None)
    monkeypatch.setattr(cli, "paper_policy_cycle_command", fake_paper_policy_cycle_command)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "roboweather",
            "paper-policy-cycle",
            "--order-mode",
            "FOK",
            "--max-slippage-cents",
            "0.03",
            "--min-post-slippage-edge",
            "0.04",
            "--entry-intent-ttl-seconds",
            "90",
            "--retry-cooldown-seconds",
            "5",
            "--max-attempts",
            "2",
        ],
    )

    cli.main()

    assert captured["order_mode"] == "FOK"
    assert captured["max_slippage_cents"] == 0.03
    assert captured["min_post_slippage_edge"] == 0.04
    assert captured["entry_intent_ttl_seconds"] == 90
    assert captured["retry_cooldown_seconds"] == 5
    assert captured["max_attempts"] == 2


def test_live_cycle_accepts_execution_options(monkeypatch) -> None:
    captured = {}

    def fake_live_cycle_command(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(cli, "ensure_directories", lambda: None)
    monkeypatch.setattr(cli, "live_cycle_command", fake_live_cycle_command)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "roboweather",
            "live-cycle",
            "--live-db",
            "/tmp/live.sqlite",
            "--mode",
            "live",
            "--max-notional-usd",
            "3",
            "--skip-allowance-check",
        ],
    )

    cli.main()

    assert captured["live_db_path"] == "/tmp/live.sqlite"
    assert captured["mode"] == "live"
    assert captured["max_notional_usd"] == 3.0
    assert captured["require_allowance_check"] is False


def test_research_loop_hook_runs_paper_promotion_after_policy_evaluation(monkeypatch) -> None:
    calls = []

    class FakeCollector:
        def __init__(self, **kwargs):
            pass

        def run_once(self):
            calls.append("collector")
            return ResearchCycleResult(
                engine_state=EngineState(
                    timestamp=utc_now_iso(),
                    mode="research",
                    discovered_markets=1,
                    actionable_signals=1,
                    orders_submitted=0,
                    skipped=0,
                    errors=[],
                ),
                snapshots_written=1,
            )

    class FakeStore:
        def latest_research_market_date(self):
            calls.append("latest_market_date")
            return "2026-05-07"

    class FakeEvaluator:
        def evaluate(self):
            calls.append("evaluator")
            return 1

    class FakePaperPolicyTrader:
        def run_once(self, market_date=None):
            calls.append(("paper", market_date))
            return type("FakePaperResult", (), {"filled": 1})()

    monkeypatch.setattr(research_collector, "ResearchCollector", FakeCollector)
    monkeypatch.setattr(research_collector, "time", type("FakeTime", (), {"time": staticmethod(lambda: 0.0), "sleep": staticmethod(lambda _: None)}))

    research_collector.run_research_loop(
        store=FakeStore(),
        model_paths=[],
        config=None,
        interval_seconds=1,
        max_cycles=1,
        policy_evaluator=FakeEvaluator(),
        paper_policy_trader=FakePaperPolicyTrader(),
    )

    assert calls == ["collector", "evaluator", "latest_market_date", ("paper", "2026-05-07")]


def test_build_dataset_accepts_explicit_stations(monkeypatch) -> None:
    captured = {}

    def fake_build_dataset_command(*args):
        captured["args"] = args

    monkeypatch.setattr(cli, "ensure_directories", lambda: None)
    monkeypatch.setattr(cli, "build_dataset_command", fake_build_dataset_command)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "roboweather",
            "build-dataset",
            "--start",
            "2022-01-01",
            "--end",
            "2025-12-31",
            "--stations",
            "KATL,KBOS,KSEA,KHOU",
        ],
    )

    cli.main()

    assert captured["args"] == ("2022-01-01", "2025-12-31", False, "KATL,KBOS,KSEA,KHOU")


def test_tune_bucket_model_parser_dispatches(monkeypatch) -> None:
    captured = {}

    def fake_tune_bucket_model_command(*args):
        captured["args"] = args

    monkeypatch.setattr(cli, "ensure_directories", lambda: None)
    monkeypatch.setattr(cli, "tune_bucket_model_command", fake_tune_bucket_model_command)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "roboweather",
            "tune-bucket-model",
            "--dataset",
            "data/raw/sample.csv",
            "--output",
            "data/reports/bucket_tuning.csv",
            "--validation-year",
            "2025",
            "--require-hrrr",
        ],
    )

    cli.main()

    assert captured["args"] == ("data/raw/sample.csv", "data/reports/bucket_tuning.csv", 2025, True, None, "high")


def test_train_bucket_model_accepts_hour_local_max(monkeypatch) -> None:
    captured = {}

    def fake_train_bucket_model_command(*args):
        captured["args"] = args

    monkeypatch.setattr(cli, "ensure_directories", lambda: None)
    monkeypatch.setattr(cli, "train_bucket_model_command", fake_train_bucket_model_command)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "roboweather",
            "train-bucket-model",
            "--dataset",
            "data/raw/sample.csv",
            "--output",
            "data/models/early.joblib",
            "--validation-year",
            "2025",
            "--hour-local-max",
            "10",
        ],
    )

    cli.main()

    assert captured["args"] == ("data/raw/sample.csv", "data/models/early.joblib", 2025, None, False, 10, "current_sigmoid", "high")


def test_catboost_bucket_model_parser_dispatches(monkeypatch) -> None:
    captured = {}

    def fake_train_catboost_bucket_model_command(*args):
        captured["args"] = args

    monkeypatch.setattr(cli, "ensure_directories", lambda: None)
    monkeypatch.setattr(cli, "train_catboost_bucket_model_command", fake_train_catboost_bucket_model_command)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "roboweather",
            "train-catboost-bucket-model",
            "--dataset",
            "data/raw/sample.csv",
            "--output",
            "data/models/catboost.joblib",
            "--report-dir",
            "data/reports/catboost",
        ],
    )

    cli.main()

    assert captured["args"] == ("data/raw/sample.csv", "data/models/catboost.joblib", 2025, "data/reports/catboost", False, None)
