from __future__ import annotations

from scripts import calibration_table


def test_entry_band_boundaries() -> None:
    assert calibration_table.entry_band(0.149) == "<0.15"
    assert calibration_table.entry_band(0.15) == "0.15-0.25"
    assert calibration_table.entry_band(0.25) == "0.25-0.35"
    assert calibration_table.entry_band(0.35) == "0.35-0.45"
    assert calibration_table.entry_band(0.45) == "0.45-0.55"
    assert calibration_table.entry_band(0.55) == ">=0.55"


def test_calibration_decision_thresholds() -> None:
    assert calibration_table.calibrate(4, 1.0, 0.15, 15, 5) == "INSUFFICIENT_DATA"
    assert calibration_table.calibrate(16, 0.15, 0.15, 15, 5) == "TRADE"
    assert calibration_table.calibrate(7, 0.05, 0.15, 15, 5) == "CANARY"
    assert calibration_table.calibrate(7, -0.05, 0.15, 15, 5) == "WATCH"
    assert calibration_table.calibrate(7, -0.10, 0.15, 15, 5) == "BLOCK"


def test_build_json_payload_single_and_all_family_shapes(monkeypatch) -> None:
    def fake_build_calibration(**kwargs):
        family = kwargs["family"]
        return {
            ("KATL" if family == "obs" else "RJTT", "BUY_NO", "0.35-0.45"): {
                "n": 6,
                "win_pct": 50.0,
                "avg_rr": -0.2,
                "model_wr": 80.0,
                "overconfidence_pp": 30.0,
                "avg_entry": 0.4,
                "decision": "BLOCK",
                "market_dates": 2,
                "_market_dates": ["2026-06-01", "2026-06-02"],
            }
        }

    monkeypatch.setattr(calibration_table, "build_calibration", fake_build_calibration)

    payload = calibration_table.build_json_payload(
        db_path="/tmp/research.sqlite",
        family="obs",
        single_model=True,
        relaxed_consensus=False,
        per_station=False,
        trade_rr=0.15,
        min_n_trade=15,
        min_n_canary=5,
    )

    assert payload["version"] == 1
    assert payload["single_model"] is True
    assert payload["thresholds"]["trade_rr"] == 0.15
    assert set(payload["families"]) == {"obs"}
    obs = payload["families"]["obs"]
    assert obs["row_count"] == 6
    assert obs["market_dates"] == 2
    assert obs["buckets"]["KATL"]["BUY_NO"]["0.35-0.45"]["decision"] == "BLOCK"
    assert "_market_dates" not in obs["buckets"]["KATL"]["BUY_NO"]["0.35-0.45"]

    all_payload = calibration_table.build_json_payload(
        db_path="/tmp/research.sqlite",
        family="all",
        single_model=True,
        relaxed_consensus=False,
        per_station=False,
        trade_rr=0.15,
        min_n_trade=15,
        min_n_canary=5,
    )

    assert set(all_payload["families"]) == set(calibration_table.MODEL_FAMILIES)
