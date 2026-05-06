from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class TradeRule:
    edge_threshold: float = 0.10


def score_signals(frame: pd.DataFrame, edge_threshold: float = 0.10) -> pd.DataFrame:
    scored = frame.copy()
    scored["signal"] = "HOLD"
    scored.loc[scored["fair_yes"] - scored["market_ask_yes"] >= edge_threshold, "signal"] = "BUY_YES"
    scored.loc[scored["market_bid_yes"] - scored["fair_yes"] >= edge_threshold, "signal"] = "BUY_NO"
    scored["trade_taken"] = scored["signal"] != "HOLD"
    scored["pnl"] = scored.apply(_trade_pnl, axis=1)
    return scored


def summarize_backtest(frame: pd.DataFrame) -> dict[str, float]:
    traded = frame.loc[frame["trade_taken"]]
    if traded.empty:
        return {"trades": 0, "total_pnl": 0.0, "avg_pnl": 0.0, "win_rate": 0.0}
    return {
        "trades": int(len(traded)),
        "total_pnl": float(traded["pnl"].sum()),
        "avg_pnl": float(traded["pnl"].mean()),
        "win_rate": float((traded["pnl"] > 0).mean()),
    }


def _trade_pnl(row: pd.Series) -> float:
    if row["signal"] == "BUY_YES":
        return 1.0 - row["market_ask_yes"] if row["target"] == 1 else -row["market_ask_yes"]
    if row["signal"] == "BUY_NO":
        no_price = 1.0 - row["market_bid_yes"]
        return 1.0 - no_price if row["target"] == 0 else -no_price
    return 0.0
