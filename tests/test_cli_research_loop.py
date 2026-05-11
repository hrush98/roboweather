from __future__ import annotations

import sys

from weather_trader import cli


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

    assert captured["args"] == ("data/raw/sample.csv", "data/reports/bucket_tuning.csv", 2025, True, None)


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

    assert captured["args"] == ("data/raw/sample.csv", "data/models/early.joblib", 2025, None, False, 10, "current_sigmoid")


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
