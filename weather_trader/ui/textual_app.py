from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Static, TabbedContent, TabPane

from weather_trader.execution.store import ExecutionStore
from weather_trader.ui.dashboard_rollups import (
    _build_live_policy_view,
    _bucket_label,
    _fmt,
    _fmt_money,
    _fmt_pct,
)


class RoboWeatherTUI(App):
    CSS = """
    Screen {
        background: #111315;
        color: #ece7dc;
    }

    #summary {
        height: 3;
        padding: 1 2;
        background: #22272b;
        color: #f8e6b0;
    }

    .section-title {
        height: 1;
        padding: 0 1;
        background: #2a3035;
        color: #f8e6b0;
    }

    TabbedContent {
        height: 1fr;
    }

    DataTable {
        height: 1fr;
        background: #15191d;
        color: #ece7dc;
    }

    #orders {
        height: 8;
    }

    #day-summary {
        height: 8;
    }

    #station-summary {
        height: 8;
    }

    #unique-exposures {
        height: 1fr;
    }

    #policy-leaderboard {
        height: 12;
    }

    #live-policy-performance {
        height: 10;
    }

    #policy-performance {
        height: 1fr;
    }

    #positions {
        height: 1fr;
    }

    #engine {
        height: 8;
    }

    #signals {
        height: 1fr;
    }

    #decisions {
        height: 1fr;
    }

    .split {
        height: 2fr;
    }

    .stack {
        height: 1fr;
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
            with TabbedContent():
                with TabPane("Overview", id="overview-tab"):
                    with Horizontal(classes="split"):
                        with Vertical(classes="stack"):
                            yield Static("Day Summary", classes="section-title")
                            yield DataTable(id="day-summary")
                        with Vertical(classes="stack"):
                            yield Static("Station Summary", classes="section-title")
                            yield DataTable(id="station-summary")
                    yield Static("Unique Exposures", classes="section-title")
                    yield DataTable(id="unique-exposures")
                    yield Static("Current Policy Positions", classes="section-title")
                    yield DataTable(id="policy-leaderboard")
                    yield Static("Live Policy MTM", classes="section-title")
                    yield DataTable(id="live-policy-performance")
                with TabPane("Execution", id="execution-tab"):
                    yield Static("Open Positions / MTM", classes="section-title")
                    yield DataTable(id="positions")
                    yield Static("Paper Orders", classes="section-title")
                    yield DataTable(id="orders")
                    yield Static("Engine Cycles", classes="section-title")
                    yield DataTable(id="engine")
                with TabPane("Pick Detail", id="pick-detail-tab"):
                    yield Static("Station / Date Candidate Detail", classes="section-title")
                    yield DataTable(id="candidates")
                with TabPane("Research", id="research-tab"):
                    yield Static("Settled Policy Performance", classes="section-title")
                    yield DataTable(id="policy-performance")
                    yield Static("Prediction Snapshots", classes="section-title")
                    yield DataTable(id="snapshots")
                    yield Static("Resolved Results By Delay Bucket", classes="section-title")
                    yield DataTable(id="results")
                with TabPane("Raw", id="raw-tab"):
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

        day_summary = self.query_one("#day-summary", DataTable)
        day_summary.cursor_type = "row"
        day_summary.add_columns("metric", "value", "detail")

        station_summary = self.query_one("#station-summary", DataTable)
        station_summary.cursor_type = "row"
        station_summary.add_columns("station", "rows", "uniq", "yes", "no", "mtm", "uniq mtm", "done")

        unique_exposures = self.query_one("#unique-exposures", DataTable)
        unique_exposures.cursor_type = "row"
        unique_exposures.add_columns(
            "station",
            "date",
            "side",
            "bucket",
            "rows",
            "entry",
            "mark",
            "pnl",
            "pnl %",
            "status",
        )

        policy_leaderboard = self.query_one("#policy-leaderboard", DataTable)
        policy_leaderboard.cursor_type = "row"
        policy_leaderboard.add_columns("policy", "model", "strategy", "delay", "rows", "wins", "done", "win rate", "mtm", "avg pnl")

        live_policy_performance = self.query_one("#live-policy-performance", DataTable)
        live_policy_performance.cursor_type = "row"
        live_policy_performance.add_columns("policy", "model", "strategy", "delay", "open", "wins", "done", "win rate", "mtm", "avg pnl")

        policy_performance = self.query_one("#policy-performance", DataTable)
        policy_performance.cursor_type = "row"
        policy_performance.add_columns(
            "policy",
            "model",
            "strategy",
            "delay",
            "resolved",
            "stations",
            "wins",
            "hit rate",
            "total pnl",
            "avg entry",
            "avg edge",
        )

        candidates = self.query_one("#candidates", DataTable)
        candidates.cursor_type = "row"
        candidates.add_columns(
            "time",
            "station",
            "date",
            "sel",
            "bucket",
            "action",
            "strategy",
            "edge yes",
            "edge no",
            "ev",
            "target",
            "max",
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

        engine = self.query_one("#engine", DataTable)
        engine.cursor_type = "row"
        engine.add_columns(
            "time",
            "mode",
            "markets",
            "actionable",
            "orders",
            "skipped",
            "errors",
            "first error",
        )

        snapshots = self.query_one("#snapshots", DataTable)
        snapshots.cursor_type = "row"
        snapshots.add_columns(
            "time",
            "station",
            "date",
            "delay",
            "obs age",
            "high",
            "pick",
            "side",
            "edge",
            "conv",
        )

        results = self.query_one("#results", DataTable)
        results.cursor_type = "row"
        results.add_columns(
            "strategy",
            "delay",
            "snapshots",
            "scored",
            "correct",
            "win rate",
            "avg edge",
            "avg pnl",
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
            position_marks = store.latest_position_marks(limit=1000)
            decisions = store.recent_decisions(limit=50)
            engine_states = store.recent_engine_states(limit=20)
            snapshots = store.recent_prediction_snapshots(limit=100)
            result_summary = store.prediction_result_summary()
            policy_performance_rows = store.policy_performance_summary()
            order_summary = store.paper_order_summary()
            live_policy_rows = store.live_research_policy_positions(limit=1000)
        finally:
            store.close()
        if self.actionable_only:
            signals = [signal for signal in signals if signal.get("signal_side") != "SKIP"]

        live_policy_view = _build_live_policy_view(live_policy_rows)
        policy_rows_sorted = live_policy_view["policy_rows"]

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

        day_summary_table = self.query_one("#day-summary", DataTable)
        day_summary_table.clear()
        day_summary_rows = [
            ("positions", str(live_policy_view["raw_count"]), "policy rows"),
            ("unique exposures", str(live_policy_view["unique_count"]), "deduped by station/date/side/bucket"),
            ("buy yes", str(live_policy_view["buy_yes"]), ""),
            ("buy no", str(live_policy_view["buy_no"]), ""),
            (
                "buy yes/no",
                f"{live_policy_view['buy_yes']} / {live_policy_view['buy_no']} ({(live_policy_view['buy_yes'] / live_policy_view['buy_no']):.2f}x)"
                if live_policy_view["buy_no"]
                else f"{live_policy_view['buy_yes']} / 0",
                "size imbalance",
            ),
            ("in money", str(live_policy_view["in_money"]), "raw positions with pnl > 0"),
            ("done95", str(live_policy_view["done"]), "selected bid at or above 0.95"),
            ("raw mtm", _fmt_money(live_policy_view["raw_mtm"]), "sum of all live policy rows"),
            ("unique mtm", _fmt_money(live_policy_view["unique_mtm"]), "sum of deduped exposures"),
            ("stations", str(live_policy_view["stations"]), "distinct stations in book"),
        ]
        for metric, value, detail in day_summary_rows:
            day_summary_table.add_row(metric, value, detail)

        station_summary_table = self.query_one("#station-summary", DataTable)
        station_summary_table.clear()
        for row in live_policy_view["station_rows"]:
            station_summary_table.add_row(
                str(row["station"]),
                str(row["raw_count"]),
                str(row["unique_count"]),
                str(row["buy_yes"]),
                str(row["buy_no"]),
                _fmt_money(row["raw_mtm"]),
                _fmt_money(row["unique_mtm"]),
                str(row["done"]),
            )

        unique_exposures_table = self.query_one("#unique-exposures", DataTable)
        unique_exposures_table.clear()
        for row in live_policy_view["exposure_rows"]:
            unique_exposures_table.add_row(
                str(row["station"]),
                str(row["market_date"]),
                str(row["side"]),
                str(row["bucket"]),
                str(row["rows"]),
                _fmt(row["entry"]),
                _fmt(row["mark"]),
                _fmt_money(row["pnl"]),
                _fmt_pct(row["pnl_pct"]),
                str(row["status"]),
            )

        policy_table = self.query_one("#policy-leaderboard", DataTable)
        policy_table.clear()
        for row in policy_rows_sorted:
            policy_table.add_row(
                str(row["policy"]),
                str(row["model_group"]),
                str(row["strategy_bucket"]),
                str(row["obs_delay_bucket"]),
                str(row["open_positions"]),
                str(row["wins"]),
                str(row["done"]),
                _fmt_pct(row["win_rate"]),
                _fmt_money(row["mtm"]),
                _fmt_money(row["avg_pnl"]),
            )

        live_policy_table = self.query_one("#live-policy-performance", DataTable)
        live_policy_table.clear()
        for row in live_policy_view["policy_rows"]:
            live_policy_table.add_row(
                str(row["policy"]),
                str(row["model_group"]),
                str(row["strategy_bucket"]),
                str(row["obs_delay_bucket"]),
                str(row["open_positions"]),
                str(row["wins"]),
                str(row["done"]),
                _fmt_pct(row["win_rate"]),
                _fmt_money(row["mtm"]),
                _fmt_money(row["avg_pnl"]),
            )

        policy_performance_table = self.query_one("#policy-performance", DataTable)
        policy_performance_table.clear()
        for row in policy_performance_rows:
            policy_performance_table.add_row(
                str(row.get("policy_name", "")),
                str(row.get("model_group", "")),
                str(row.get("strategy_bucket", "")),
                str(row.get("obs_delay_bucket", "")),
                str(row.get("resolved_positions", "")),
                str(row.get("station_days", "")),
                str(row.get("wins", "")),
                _fmt_pct(row.get("hit_rate")),
                _fmt_money(row.get("total_pnl")),
                _fmt_money(row.get("avg_entry")),
                _fmt(row.get("avg_edge")),
            )

        candidates_table = self.query_one("#candidates", DataTable)
        candidates_table.clear()
        for group in groups[:20]:
            selected_market_id = group.get("selected_market_id")
            for candidate in group.get("candidates") or []:
                candidates_table.add_row(
                    str(group.get("timestamp", ""))[11:19],
                    str(group.get("station", "")),
                    str(group.get("market_date", "")),
                    "*" if candidate.get("market_id") == selected_market_id else "",
                    str(candidate.get("bucket", "")),
                    str(candidate.get("action", "")),
                    str(candidate.get("strategy_bucket", "")),
                    _fmt(candidate.get("edge_yes")),
                    _fmt(candidate.get("edge_no")),
                    _fmt(candidate.get("expected_value")),
                    _fmt_money(candidate.get("target_usd")),
                    _fmt(candidate.get("max_price")),
                    ",".join(candidate.get("skip_reasons") or [])[:44],
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

        engine_table = self.query_one("#engine", DataTable)
        engine_table.clear()
        for state in engine_states:
            errors = state.get("errors") or []
            engine_table.add_row(
                str(state.get("timestamp", ""))[11:19],
                str(state.get("mode", "")),
                str(state.get("discovered_markets", "")),
                str(state.get("actionable_signals", "")),
                str(state.get("orders_submitted", "")),
                str(state.get("skipped", "")),
                str(len(errors)),
                str(errors[0] if errors else "")[:80],
            )

        snapshots_table = self.query_one("#snapshots", DataTable)
        snapshots_table.clear()
        for snapshot in snapshots:
            snapshots_table.add_row(
                str(snapshot.get("decision_time_local", ""))[11:19],
                str(snapshot.get("station", "")),
                str(snapshot.get("market_date", "")),
                str(snapshot.get("obs_delay_bucket", "")),
                _fmt(snapshot.get("obs_age_minutes")),
                _fmt(snapshot.get("high_so_far")),
                str(snapshot.get("selected_bucket") or ""),
                str(snapshot.get("selected_side", "")),
                _fmt(snapshot.get("selected_edge")),
                "Y" if snapshot.get("high_conviction") else "",
            )

        results_table = self.query_one("#results", DataTable)
        results_table.clear()
        for row in result_summary:
            results_table.add_row(
                str(row.get("strategy_bucket", "")),
                str(row.get("obs_delay_bucket", "")),
                str(row.get("snapshots", "")),
                str(row.get("scored", "")),
                str(row.get("correct", "")),
                _fmt_pct(row.get("win_rate")),
                _fmt(row.get("avg_edge")),
                _fmt(row.get("avg_pnl")),
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
                    f"raw {live_policy_view['raw_count']} / unique {live_policy_view['unique_count']} / itm {live_policy_view['in_money']} / done {live_policy_view['done']}",
                    f"mtm raw {_fmt_money(live_policy_view['raw_mtm'])} / unique {_fmt_money(live_policy_view['unique_mtm'])}",
                    f"buy yes/no {live_policy_view['buy_yes']}/{live_policy_view['buy_no']}",
                    f"signals {len(signals)} / picks {len(groups)} / snapshots {len(snapshots)} / orders {order_summary.get('orders', 0)} / live policies {len(live_policy_view['policy_rows'])}",
                    f"filled {order_summary.get('filled', 0)} / rejected {order_summary.get('rejected', 0)} / paper cost ${float(order_summary.get('total_cost') or 0.0):.2f}",
                    f"policy rows {len(live_policy_rows)} / policies {len(policy_rows_sorted)} / settled silos {len(policy_performance_rows)} / latest live policy {live_policy_view['latest_position_time']}",
                    f"actionable_only={self.actionable_only}",
                ]
            )
        )
