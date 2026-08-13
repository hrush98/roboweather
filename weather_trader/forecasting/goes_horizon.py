"""Frozen horizon identities for independent F5 GOES evidence arms."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Sequence

from weather_trader.forecasting.evaluation import EvaluationContract
from weather_trader.forecasting.goes_model import GoesHeatingModelContract
from weather_trader.forecasting.remaining_heating import (
    RemainingHeatingContract,
    exact_cutoff_multinomial_contract,
)


@dataclass(frozen=True)
class GoesHorizonContract:
    hour_local: int
    horizon_id: str
    predecessor_version: str
    goes_model_version: str
    collection_activation_date: str
    predecessor_artifact_directory: str
    report_directory: str


HORIZON_CONTRACTS: dict[int, GoesHorizonContract] = {
    12: GoesHorizonContract(
        hour_local=12,
        horizon_id="d0_exact_12_local",
        predecessor_version="remaining_heating_hurdle_multinomial_exact_12_local_v1",
        goes_model_version="goes_dsr_market_relative_logit_exact_12_local_v1",
        collection_activation_date="2026-08-14",
        predecessor_artifact_directory="reports/forecast-edge/f5-h12-predecessor",
        report_directory="reports/forecast-edge/f5-h12-current",
    ),
    14: GoesHorizonContract(
        hour_local=14,
        horizon_id="d0_exact_14_local",
        predecessor_version="remaining_heating_hurdle_multinomial_exact_cutoff_v3",
        goes_model_version="goes_dsr_market_relative_logit_exact_14_local_v1",
        collection_activation_date="2026-08-14",
        predecessor_artifact_directory="reports/forecast-edge/f3-current",
        report_directory="reports/forecast-edge/f5-current",
    ),
}


def horizon_contract(hour_local: int) -> GoesHorizonContract:
    try:
        return HORIZON_CONTRACTS[int(hour_local)]
    except KeyError as exc:
        supported = ", ".join(str(value) for value in sorted(HORIZON_CONTRACTS))
        raise ValueError(
            f"unsupported F5 horizon {hour_local}; frozen horizons are {supported}"
        ) from exc


def predecessor_evaluation_contract(hour_local: int) -> EvaluationContract:
    horizon = horizon_contract(hour_local)
    return EvaluationContract(horizon_hour_local=horizon.hour_local)


def predecessor_model_contract(hour_local: int) -> RemainingHeatingContract:
    horizon = horizon_contract(hour_local)
    return replace(
        exact_cutoff_multinomial_contract(),
        version=horizon.predecessor_version,
    )


def goes_model_contract(hour_local: int) -> GoesHeatingModelContract:
    horizon = horizon_contract(hour_local)
    return GoesHeatingModelContract(
        version=horizon.goes_model_version,
        horizon_id=horizon.horizon_id,
    )


def chronological_fit_holdout_dates(
    values: Sequence[object], fit_fraction: float = 0.60
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    dates = tuple(sorted({str(value) for value in values}))
    if len(dates) < 2:
        raise ValueError("horizon predecessor needs at least two weather dates")
    if not 0.0 < fit_fraction < 1.0:
        raise ValueError("fit fraction must be strictly between zero and one")
    cutoff = min(max(1, int(len(dates) * fit_fraction)), len(dates) - 1)
    return dates[:cutoff], dates[cutoff:]
