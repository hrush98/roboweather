from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
MODELS_DIR = DATA_DIR / "models"
REPORTS_DIR = DATA_DIR / "reports"
PAPER_DIR = DATA_DIR / "paper"
CACHE_DIR = DATA_DIR / "cache"


@dataclass(frozen=True)
class TrainingWindow:
    train_start_year: int = 2022
    train_end_year: int = 2024
    validation_year: int = 2025


def ensure_directories() -> None:
    for directory in (DATA_DIR, RAW_DIR, PROCESSED_DIR, MODELS_DIR, REPORTS_DIR, PAPER_DIR, CACHE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
