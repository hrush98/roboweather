from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODELS_DIR = ROOT / "data" / "models"
DEFAULT_REPORTS_DIR = ROOT / "data" / "reports"
DEFAULT_CSV = DEFAULT_REPORTS_DIR / "model_registry.csv"
DEFAULT_MD = ROOT / "docs" / "model-performance-log.md"

METRIC_COLUMNS = [
    "accuracy",
    "brier_score",
    "log_loss",
    "roc_auc",
    "groups",
    "grouped_log_loss",
    "grouped_brier_score",
    "top_bucket_accuracy",
    "mae",
    "rmse",
    "avg_predicted_sigma",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build model registry CSV and Markdown log from local model artifacts.")
    parser.add_argument("--models-dir", default=str(DEFAULT_MODELS_DIR))
    parser.add_argument("--reports-dir", default=str(DEFAULT_REPORTS_DIR))
    parser.add_argument("--csv", default=str(DEFAULT_CSV))
    parser.add_argument("--md", default=str(DEFAULT_MD))
    args = parser.parse_args()

    rows = scan_models(Path(args.models_dir), Path(args.reports_dir))
    write_csv(rows, Path(args.csv))
    write_markdown(rows, Path(args.md), Path(args.csv))
    print(f"wrote {args.csv} ({len(rows)} rows)")
    print(f"wrote {args.md}")


def scan_models(models_dir: Path, reports_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).isoformat()
    for path in sorted(models_dir.glob("*.joblib")):
        name = path.stem
        row: dict[str, Any] = {
            "model_name": name,
            "artifact_path": rel(path),
            "report_dir": rel(reports_dir / name) if (reports_dir / name).exists() else "",
            "artifact_mtime_utc": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            "registry_updated_utc": now,
            "model_type": "",
            "market_family": "",
            "train_rows": "",
            "validation_rows": "",
            "feature_count": "",
            "feature_set": classify_feature_set(name, []),
            "feature_columns": "",
            "load_error": "",
        }
        try:
            bundle = joblib.load(path)
            metrics = bundle.get("metrics") or {}
            feature_columns = list(bundle.get("feature_columns") or [])
            row.update(
                {
                    "model_type": bundle.get("model_type") or infer_model_type(name),
                    "market_family": bundle.get("market_family") or bundle.get("temperature_metric") or "",
                    "train_rows": bundle.get("train_rows") or "",
                    "validation_rows": bundle.get("validation_rows") or "",
                    "feature_count": len(feature_columns),
                    "feature_set": classify_feature_set(name, feature_columns),
                    "feature_columns": " ".join(feature_columns),
                }
            )
            for metric in METRIC_COLUMNS:
                row[metric] = metrics.get(metric, "")
        except Exception as exc:  # noqa: BLE001 - registry should survive incompatible old artifacts.
            row["model_type"] = infer_model_type(name)
            row["load_error"] = f"{type(exc).__name__}: {exc}"
            for metric in METRIC_COLUMNS:
                row[metric] = ""
        rows.append(row)
    return rows


def classify_feature_set(model_name: str, feature_columns: list[str]) -> str:
    text = " ".join([model_name, *feature_columns]).lower()
    has_hrrr = "hrrr" in text
    has_hrrr_rich = any(token in text for token in ["hrrr_shortwave", "hrrr_gust", "hrrr_cloud_cover", "hrrr_dewpoint_current", "hrrr_rh_current"])
    has_metar = any(token in text for token in ["relative_humidity", "pressure_mslp", "wet_bulb", "visibility_miles", "precip_1h"])
    if has_metar and has_hrrr_rich:
        return "metar_rich+hrrr_rich"
    if has_metar and has_hrrr:
        return "metar_rich+hrrr_basic"
    if has_metar:
        return "metar_rich"
    if has_hrrr_rich:
        return "hrrr_rich"
    if has_hrrr:
        return "hrrr_basic"
    return "obs"


def infer_model_type(model_name: str) -> str:
    if model_name.startswith("catboost_bucket"):
        return "catboost_bucket"
    if model_name.startswith("dynamic_bucket") or model_name.startswith("low_dynamic_bucket"):
        return "dynamic_bucket"
    if model_name.startswith("high_regression"):
        return "high_regression_empirical_residual"
    if model_name.startswith("ngboost_normal"):
        return "ngboost_normal_crps"
    if model_name.startswith("mvp") or model_name.startswith("low_mvp"):
        return "threshold_classifier"
    return "unknown"


def write_csv(rows: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "model_name",
        "model_type",
        "feature_set",
        "market_family",
        "train_rows",
        "validation_rows",
        *METRIC_COLUMNS,
        "feature_count",
        "artifact_path",
        "report_dir",
        "artifact_mtime_utc",
        "registry_updated_utc",
        "load_error",
        "feature_columns",
    ]
    with output.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def write_markdown(rows: list[dict[str, Any]], output: Path, csv_path: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    loaded = [row for row in rows if not row.get("load_error")]
    errored = [row for row in rows if row.get("load_error")]
    interesting = sorted(
        loaded,
        key=lambda row: (
            row.get("feature_set") or "",
            float_or_inf(row.get("grouped_log_loss")),
            float_or_inf(row.get("log_loss")),
            float_or_inf(row.get("mae")),
            row.get("model_name") or "",
        ),
    )
    lines = [
        "# Model Performance Log",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"Machine-readable registry: `{rel(csv_path)}`",
        "",
        "## PM-Active US12 Enrichment Comparison",
        "",
        "| model | type | feature set | validation rows | log loss | grouped log loss | top bucket acc | MAE | RMSE |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in pm_active_comparison_rows(loaded):
        lines.append(
            "| {model} | {typ} | {features} | {val} | {log_loss} | {gll} | {top} | {mae} | {rmse} |".format(
                model=row.get("model_name", ""),
                typ=row.get("model_type", ""),
                features=row.get("feature_set", ""),
                val=row.get("validation_rows", ""),
                log_loss=fmt(row.get("log_loss")),
                gll=fmt(row.get("grouped_log_loss")),
                top=fmt(row.get("top_bucket_accuracy")),
                mae=fmt(row.get("mae")),
                rmse=fmt(row.get("rmse")),
            )
        )
    lines.extend([
        "",
        "## Best Loaded Artifacts",
        "",
        "| model | type | feature set | validation rows | primary metric | top bucket acc | report |",
        "|---|---|---:|---:|---:|---:|---|",
    ])
    for row in interesting[:40]:
        metric = primary_metric(row)
        lines.append(
            "| {model} | {typ} | {features} | {val} | {metric} | {top} | {report} |".format(
                model=row.get("model_name", ""),
                typ=row.get("model_type", ""),
                features=row.get("feature_set", ""),
                val=row.get("validation_rows", ""),
                metric=metric,
                top=fmt(row.get("top_bucket_accuracy")),
                report=row.get("report_dir", ""),
            )
        )
    lines.extend([
        "",
        "## Notes",
        "",
        "- `grouped_log_loss` is the primary metric for bucket-distribution models; lower is better.",
        "- `log_loss`/`brier_score` apply to threshold classifier artifacts; lower is better.",
        "- `mae`/`rmse` apply to final-high distribution/regression artifacts; lower is better.",
        "- Artifacts with load errors are retained in the CSV so old training history remains visible even when sklearn/joblib compatibility prevents metric extraction.",
    ])
    if errored:
        lines.extend(["", "## Load Errors", "", "| model | inferred type | error |", "|---|---|---|"])
        for row in errored[:30]:
            lines.append(f"| {row.get('model_name')} | {row.get('model_type')} | {str(row.get('load_error')).replace('|', '/')} |")
    output.write_text("\n".join(lines) + "\n")


def pm_active_comparison_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [
        row
        for row in rows
        if "pm_active_us12" in str(row.get("model_name", ""))
        and row.get("feature_set") in {"obs", "metar_rich", "hrrr_rich", "metar_rich+hrrr_rich"}
        and row.get("model_type")
        in {
            "threshold_classifier",
            "dynamic_bucket",
            "catboost_bucket",
            "high_regression_empirical_residual",
            "ngboost_normal_crps",
        }
    ]
    return sorted(
        selected,
        key=lambda row: (
            model_type_order(row.get("model_type")),
            feature_set_order(row.get("feature_set")),
            row.get("model_name") or "",
        ),
    )


def model_type_order(value: Any) -> int:
    order = {
        "threshold_classifier": 0,
        "dynamic_bucket": 1,
        "catboost_bucket": 2,
        "high_regression_empirical_residual": 3,
        "ngboost_normal_crps": 4,
    }
    return order.get(str(value), 99)


def feature_set_order(value: Any) -> int:
    order = {
        "obs": 0,
        "metar_rich": 1,
        "hrrr_rich": 2,
        "metar_rich+hrrr_rich": 3,
    }
    return order.get(str(value), 99)


def primary_metric(row: dict[str, Any]) -> str:
    for key in ["grouped_log_loss", "log_loss", "mae"]:
        value = row.get(key)
        if value not in (None, ""):
            return f"{key}={fmt(value)}"
    return ""


def fmt(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return str(value)


def float_or_inf(value: Any) -> float:
    try:
        if value in (None, ""):
            return float("inf")
        return float(value)
    except (TypeError, ValueError):
        return float("inf")


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    main()
