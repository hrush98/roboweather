from __future__ import annotations

import pandas as pd

from weather_trader.backtest.synthetic_threshold_backtest import score_signals, summarize_backtest


def test_backtest_scoring_produces_signals_and_summary() -> None:
    frame = pd.DataFrame(
        {
            "fair_yes": [0.8, 0.2, 0.5],
            "market_ask_yes": [0.6, 0.3, 0.45],
            "market_bid_yes": [0.7, 0.4, 0.48],
            "target": [1, 0, 1],
        }
    )
    scored = score_signals(frame, edge_threshold=0.1)
    summary = summarize_backtest(scored)
    assert "signal" in scored.columns
    assert summary["trades"] == 2
