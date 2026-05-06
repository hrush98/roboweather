from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Footer, Header, Static

from weather_trader.execution.store import ExecutionStore


class RoboWeatherTUI(App):
    CSS = """
    Screen {
        background: #10140f;
        color: #e7ead7;
    }

    #summary {
        height: 3;
        padding: 1 2;
        background: #27301f;
        color: #f7f1c5;
    }

    DataTable {
        height: 1fr;
        background: #141a12;
    }

    #orders {
        height: 9;
    }

    #decisions {
        height: 9;
    }

    #positions {
        height: 9;
    }

    #groups {
        height: 9;
    }
    """
    BINDINGS = [
        ("r", "refresh", "Refresh"),
        ("f", "toggle_actionable", "Actionable"),
        ("/", "focus_filter", "Search"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, db_path: Path) -> None:
        super().__init__()
        self.db_path = db_path
        self.actionable_only = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            yield Static("roboweather paper harness", id="summary")
            yield Static("Paper orders", classes="section-title")
            yield DataTable(id="orders")
            yield Static("Station / Date Decisions", classes="section-title")
            yield DataTable(id="groups")
            yield Static("Positions / MTM", classes="section-title")
            yield DataTable(id="positions")
            yield Static("Decisions", classes="section-title")
            yield DataTable(id="decisions")
            yield Static("Signals", classes="section-title")
            yield DataTable(id="signals")
        yield Footer()

    def on_mount(self) -> None:
        orders = self.query_one("#orders", DataTable)
        orders.cursor_type = "row"
        orders.add_columns(
            "time",
            "action",
            "state",
            "cost",
            "shares",
            "avg",
            "max",
            "reject",
            "market",
        )

        groups = self.query_one("#groups", DataTable)
        groups.cursor_type = "row"
        groups.add_columns(
            "time",
            "station",
            "date",
            "cands",
            "selected",
            "strategy",
            "edge",
            "bucket",
            "skip",
        )

        positions = self.query_one("#positions", DataTable)
        positions.cursor_type = "row"
        positions.add_columns(
            "time",
            "station",
            "bucket",
            "side",
            "entry",
            "bid",
            "cost",
            "mtm",
            "pnl",
            "pnl %",
            "status",
        )

        decisions = self.query_one("#decisions", DataTable)
        decisions.cursor_type = "row"
        decisions.add_columns(
            "time",
            "action",
            "strategy",
            "target",
            "max",
            "ev",
            "skip",
            "market",
        )

        signals = self.query_one("#signals", DataTable)
        signals.cursor_type = "row"
        signals.add_columns(
            "time",
            "station",
            "bucket",
            "fair yes",
            "yes ask",
            "fair no",
            "no ask",
            "edge",
            "signal",
            "reason",
        )
        self.refresh_table()
        self.set_interval(10.0, self.refresh_table)

    def action_refresh(self) -> None:
        self.refresh_table()

    def action_toggle_actionable(self) -> None:
        self.actionable_only = not self.actionable_only
        self.refresh_table()

    def action_focus_filter(self) -> None:
        self.notify("Search/filter input is not implemented yet; use actionable toggle for now.")

    def refresh_table(self) -> None:
        store = ExecutionStore(self.db_path)
        try:
            signals = store.recent_signals(limit=200)
            orders = store.recent_paper_orders(limit=50)
            groups = store.recent_station_date_decisions(limit=50)
            position_marks = store.latest_position_marks(limit=50)
            decisions = store.recent_decisions(limit=50)
            order_summary = store.paper_order_summary()
            open_positions = store.recent_positions()
        finally:
            store.close()
        if self.actionable_only:
            signals = [signal for signal in signals if signal.get("signal_side") != "SKIP"]

        orders_table = self.query_one("#orders", DataTable)
        orders_table.clear()
        for order in orders:
            orders_table.add_row(
                str(order.get("timestamp", ""))[11:19],
                str(order.get("action", "")),
                str(order.get("state", "")),
                _fmt_money(order.get("cost")),
                _fmt(order.get("filled_shares")),
                _fmt(order.get("avg_price")),
                _fmt(order.get("max_price")),
                str(order.get("reject_reason") or "")[:18],
                str(order.get("market_id", ""))[:14],
            )

        groups_table = self.query_one("#groups", DataTable)
        groups_table.clear()
        for group in groups:
            selected_market_id = group.get("selected_market_id")
            selected_bucket = ""
            for candidate in group.get("candidates") or []:
                if candidate.get("market_id") == selected_market_id:
                    selected_bucket = str(candidate.get("bucket", ""))
                    break
            groups_table.add_row(
                str(group.get("timestamp", ""))[11:19],
                str(group.get("station", "")),
                str(group.get("market_date", "")),
                str(group.get("candidate_count", "")),
                str(group.get("selected_action", "")),
                str(group.get("selected_strategy_bucket", "")),
                _fmt(group.get("selected_edge")),
                selected_bucket,
                str(group.get("skip_reason") or "")[:28],
            )

        positions_table = self.query_one("#positions", DataTable)
        positions_table.clear()
        for mark in position_marks:
            positions_table.add_row(
                str(mark.get("timestamp", ""))[11:19],
                str(mark.get("station", "")),
                _bucket_label(mark.get("lower_f"), mark.get("upper_f")),
                str(mark.get("side", "")),
                _fmt(mark.get("avg_entry_price")),
                _fmt(mark.get("current_bid")),
                _fmt_money(mark.get("cost")),
                _fmt_money(mark.get("mark_value")),
                _fmt_money(mark.get("unrealized_pnl")),
                _fmt_pct(mark.get("unrealized_pnl_pct")),
                str(mark.get("effective_status", "")),
            )

        decisions_table = self.query_one("#decisions", DataTable)
        decisions_table.clear()
        for decision in decisions:
            decisions_table.add_row(
                str(decision.get("timestamp", ""))[11:19],
                str(decision.get("action", "")),
                str(decision.get("strategy_bucket", "")),
                _fmt_money(decision.get("target_usd")),
                _fmt(decision.get("max_price")),
                _fmt(decision.get("expected_value")),
                ",".join(decision.get("skip_reasons") or [])[:42],
                str(decision.get("market_id", ""))[:14],
            )

        signals_table = self.query_one("#signals", DataTable)
        signals_table.clear()
        for signal in signals:
            edge_yes = signal.get("edge_yes")
            edge_no = signal.get("edge_no")
            best_edge = max(
                edge_yes if edge_yes is not None else float("-inf"),
                edge_no if edge_no is not None else float("-inf"),
            )
            signals_table.add_row(
                str(signal.get("timestamp", ""))[11:19],
                str(signal.get("station", "")),
                _bucket_label(signal.get("lower_f"), signal.get("upper_f")),
                _fmt(signal.get("fair_yes")),
                _fmt(signal.get("yes_ask")),
                _fmt(signal.get("fair_no")),
                _fmt(signal.get("no_ask")),
                _fmt(best_edge),
                str(signal.get("signal_side", "")),
                ",".join(signal.get("reason_codes") or [])[:80],
            )
        summary = self.query_one("#summary", Static)
        summary.update(
            " | ".join(
                [
                    f"{len(signals)} signals",
                    f"{order_summary.get('orders', 0)} orders",
                    f"{order_summary.get('filled', 0)} filled",
                    f"{order_summary.get('rejected', 0)} rejected",
                    f"${float(order_summary.get('total_cost') or 0.0):.2f} paper cost",
                    f"{len(open_positions)} open positions",
                    f"actionable_only={self.actionable_only}",
                ]
            )
        )


def _fmt(value) -> str:
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError, OverflowError):
        return ""


def _fmt_money(value) -> str:
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError, OverflowError):
        return ""


def _fmt_pct(value) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError, OverflowError):
        return ""


def _bucket_label(lower_f, upper_f) -> str:
    if lower_f is not None and upper_f is not None:
        return f"{float(lower_f):g}-{float(upper_f):g}F"
    if lower_f is not None:
        return f">={float(lower_f):g}F"
    if upper_f is not None:
        return f"<={float(upper_f):g}F"
    return "unknown"
