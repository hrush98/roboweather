from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Footer, Header, Static, TabbedContent, TabPane

from weather_trader.execution.store import ExecutionStore
from weather_trader.live.settings import load_live_settings
from weather_trader.ui.dashboard_rollups import (
    _build_live_policy_view,
    _bucket_label,
    _fmt,
    _fmt_money,
    _fmt_pct,
    _money_text,
    _status_text,
)
from weather_trader.ui.process_supervisor import ProcessSpec, ProcessSupervisor


CONFIG_STATIONS = {"KATL", "KBKF", "KDAL", "KDEN", "KHOU", "KLAX", "KLGA", "KMIA", "KORD", "KSEA", "KSFO"}


@dataclass(frozen=True)
class TableSort:
    column_key: Any
    reverse: bool


OVERVIEW_TABLE_IDS = {"live-summary", "live-strategies", "live-stations", "live-positions"}
REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_DESC_SORT_COLUMNS = {
    "attempts",
    "avg_bid",
    "avg_edge",
    "avg_entry",
    "avg_fair",
    "cost",
    "edge",
    "entry",
    "expected_rr",
    "filled",
    "live_minus_exp",
    "live_rr",
    "mark_pct",
    "mtm",
    "open_positions",
    "pnl",
    "risk",
    "target",
}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_minutes(value: str | None) -> float | None:
    dt = _parse_dt(value)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 60.0


def _sort_key(value: Any, reverse: bool = False) -> tuple[int, int, float | str]:
    text = str(value or "").strip()
    if text.lower() in {"", "n/a", "none", "nan"}:
        return (0 if reverse else 1, 0, "")
    numeric_text = text.replace("$", "").replace("%", "").replace(",", "").strip()
    try:
        return (1 if reverse else 0, 0, float(numeric_text))
    except ValueError:
        return (1 if reverse else 0, 1, text.casefold())


def _add_keyed_columns(table: DataTable, columns: list[tuple[str, str]]) -> None:
    for label, key in columns:
        table.add_column(label, key=key)


def _status_priority(status: str) -> int:
    return {
        "STOPPED": 0,
        "KILL_SWITCH": 0,
        "REJECTED": 1,
        "FAILED": 1,
        "LIVE_STRESS": 1,
        "NO_BOOK_MARK": 2,
        "BOOK_GAPS": 2,
        "RESERVED": 3,
        "SUBMITTED": 3,
        "PARTIAL": 4,
        "FILLED": 5,
        "LIVE": 5,
        "LIVE_STRONG": 6,
        "MARKED": 6,
    }.get(status, 9)


def _default_target_date(db_path: Path) -> str:
    store = ExecutionStore(db_path)
    try:
        latest = store.latest_live_market_date() or store.latest_research_market_date()
    finally:
        store.close()
    return latest or datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


def _default_process_supervisor() -> ProcessSupervisor:
    runner = REPO_ROOT / "scripts" / "run_research.sh"
    return ProcessSupervisor(
        [
            ProcessSpec("research", "Research Loop", (str(runner), "loop")),
            ProcessSpec("live", "Live Loop", (str(runner), "live-loop")),
        ],
        cwd=REPO_ROOT,
    )


def _fmt_uptime(seconds: float | None) -> str:
    if seconds is None:
        return ""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


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

    #kill-panel {
        height: 3;
        background: #1b1e21;
    }

    #kill-status {
        width: 1fr;
        padding: 1 2;
        background: #263034;
        color: #ece7dc;
    }

    #kill-button {
        width: 18;
        margin: 0 1;
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

    #live-summary {
        height: 11;
    }

    #live-strategies {
        height: 12;
    }

    #live-stations {
        height: 12;
    }

    #live-positions {
        height: 1fr;
    }

    #live-orders {
        height: 1fr;
    }

    #live-events {
        height: 1fr;
    }

    #process-table {
        height: 7;
    }

    #research-log {
        height: 1fr;
    }

    #live-log {
        height: 1fr;
    }

    .process-controls {
        height: 3;
    }

    .process-controls Button {
        margin: 0 1;
    }

    #engine {
        height: 8;
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
        ("ctrl+k", "activate_kill_switch", "KILL"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, db_path: Path, process_supervisor: ProcessSupervisor | None = None) -> None:
        super().__init__()
        self.db_path = db_path
        self.actionable_only = False
        self.target_date = _default_target_date(db_path)
        self.kill_switch_path = Path(load_live_settings().live_kill_switch_path).expanduser()
        self.table_sorts: dict[str, TableSort] = {}
        self.process_supervisor = process_supervisor or _default_process_supervisor()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            yield Static("roboweather live trading", id="summary")
            with Horizontal(id="kill-panel"):
                yield Static("", id="kill-status")
                yield Button("KILL TRADING", id="kill-button", variant="error")
            with TabbedContent():
                with TabPane("Live", id="live-tab"):
                    yield Static("Live Risk", classes="section-title")
                    yield DataTable(id="live-summary")
                    yield Static("Strategies", classes="section-title")
                    yield DataTable(id="live-strategies")
                    with Horizontal(classes="split"):
                        with Vertical(classes="stack"):
                            yield Static("Stations", classes="section-title")
                            yield DataTable(id="live-stations")
                        with Vertical(classes="stack"):
                            yield Static("Open Exposure", classes="section-title")
                            yield DataTable(id="live-positions")
                with TabPane("Orders", id="orders-tab"):
                    yield Static("Live Order Attempts", classes="section-title")
                    yield DataTable(id="live-orders")
                    yield Static("Live Trade Events", classes="section-title")
                    yield DataTable(id="live-events")
                with TabPane("Processes", id="processes-tab"):
                    yield Static("Process Supervisor", classes="section-title")
                    with Horizontal(classes="process-controls"):
                        yield Button("Start Research", id="start-research", variant="success")
                        yield Button("Stop Research", id="stop-research", variant="warning")
                        yield Button("Start Live Dry Run", id="start-live", variant="success")
                        yield Button("Stop Live", id="stop-live", variant="warning")
                    yield DataTable(id="process-table")
                    with Horizontal(classes="split"):
                        with Vertical(classes="stack"):
                            yield Static("Research Log", classes="section-title")
                            yield DataTable(id="research-log")
                        with Vertical(classes="stack"):
                            yield Static("Live Log", classes="section-title")
                            yield DataTable(id="live-log")
                with TabPane("Diagnostics", id="diagnostics-tab"):
                    yield Static("Engine Cycles", classes="section-title")
                    yield DataTable(id="engine")
                    yield Static("Recent Decisions", classes="section-title")
                    yield DataTable(id="decisions")
                    yield Static("Recent Signals", classes="section-title")
                    yield DataTable(id="signals")
        yield Footer()

    def on_mount(self) -> None:
        live_summary = self.query_one("#live-summary", DataTable)
        live_summary.cursor_type = "row"
        _add_keyed_columns(live_summary, [("metric", "metric"), ("value", "value"), ("detail", "detail")])

        live_strategies = self.query_one("#live-strategies", DataTable)
        live_strategies.cursor_type = "row"
        _add_keyed_columns(
            live_strategies,
            [
                ("status", "status"),
                ("book", "book_status"),
                ("strategy", "policy"),
                ("pos", "open_positions"),
                ("risk", "risk"),
                ("mtm", "mtm"),
                ("avgPx", "avg_entry"),
                ("avgFair", "avg_fair"),
                ("avgEdge", "avg_edge"),
                ("liveBid", "avg_bid"),
                ("quote%", "mark_pct"),
                ("exp R/R", "expected_rr"),
                ("live R/R", "live_rr"),
            ],
        )

        live_stations = self.query_one("#live-stations", DataTable)
        live_stations.cursor_type = "row"
        _add_keyed_columns(
            live_stations,
            [
                ("station", "station"),
                ("status", "status"),
                ("pos", "positions"),
                ("risk", "risk"),
                ("mtm", "mtm"),
                ("live R/R", "live_rr"),
                ("book", "book_status"),
                ("quote%", "mark_pct"),
                ("yes", "buy_yes"),
                ("no", "buy_no"),
            ],
        )

        live_positions = self.query_one("#live-positions", DataTable)
        live_positions.cursor_type = "row"
        _add_keyed_columns(
            live_positions,
            [
                ("time", "time"),
                ("state", "state"),
                ("strategy", "strategy"),
                ("station", "station"),
                ("date", "date"),
                ("side", "side"),
                ("bucket", "bucket"),
                ("target", "target"),
                ("filled", "filled"),
                ("cost", "cost"),
                ("entry", "entry"),
                ("bid", "bid"),
                ("pnl", "pnl"),
                ("live R/R", "live_rr"),
                ("weather", "weather"),
            ],
        )

        live_orders = self.query_one("#live-orders", DataTable)
        live_orders.cursor_type = "row"
        live_orders.add_columns("time", "strategy", "station", "side", "mode", "limit", "target", "filled", "avg", "state", "reason", "order")

        live_events = self.query_one("#live-events", DataTable)
        live_events.cursor_type = "row"
        live_events.add_columns("time", "strategy", "type", "message", "position")

        process_table = self.query_one("#process-table", DataTable)
        process_table.cursor_type = "row"
        process_table.add_columns("process", "status", "pid", "uptime", "exit", "restarts", "latest")

        research_log = self.query_one("#research-log", DataTable)
        research_log.cursor_type = "row"
        research_log.add_columns("line")

        live_log = self.query_one("#live-log", DataTable)
        live_log.cursor_type = "row"
        live_log.add_columns("line")

        engine = self.query_one("#engine", DataTable)
        engine.cursor_type = "row"
        engine.add_columns("time", "mode", "markets", "actionable", "orders", "skipped", "errors", "first error")

        decisions = self.query_one("#decisions", DataTable)
        decisions.cursor_type = "row"
        decisions.add_columns("time", "action", "strategy", "target", "max", "ev", "skip", "market")

        signals = self.query_one("#signals", DataTable)
        signals.cursor_type = "row"
        signals.add_columns("time", "station", "bucket", "fair yes", "yes ask", "fair no", "no ask", "edge", "signal", "reason")

        self.refresh_table()
        self.refresh_processes()
        self.set_interval(10.0, self.refresh_table)
        self.set_interval(1.0, self.refresh_processes)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "kill-button":
            self.action_activate_kill_switch()
        elif event.button.id == "start-research":
            await self._start_process("research")
        elif event.button.id == "stop-research":
            await self._stop_process("research")
        elif event.button.id == "start-live":
            await self._start_process("live")
        elif event.button.id == "stop-live":
            await self._stop_process("live")

    async def on_unmount(self) -> None:
        await self.process_supervisor.stop_all()

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        table = event.data_table
        table_id = str(table.id or "")
        if table_id not in OVERVIEW_TABLE_IDS:
            return
        column_key = event.column_key
        column_name = str(getattr(column_key, "value", column_key))
        current = self.table_sorts.get(table_id)
        reverse = not current.reverse if current and current.column_key == column_key else column_name in DEFAULT_DESC_SORT_COLUMNS
        self.table_sorts[table_id] = TableSort(column_key=column_key, reverse=reverse)
        self._apply_table_sort(table)
        direction = "descending" if reverse else "ascending"
        self.notify(f"Sorted {event.label} {direction}.")

    def _apply_table_sort(self, table: DataTable) -> None:
        table_id = str(table.id or "")
        sort = self.table_sorts.get(table_id)
        if sort is None:
            return
        table.sort(sort.column_key, key=lambda value: _sort_key(value, reverse=sort.reverse), reverse=sort.reverse)

    def action_refresh(self) -> None:
        self.refresh_table()

    def action_toggle_actionable(self) -> None:
        self.actionable_only = not self.actionable_only
        self.refresh_table()

    def action_activate_kill_switch(self) -> None:
        self.kill_switch_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.kill_switch_path.exists():
            self.kill_switch_path.write_text(f"activated_at={datetime.now(timezone.utc).isoformat()}\n")
        self.notify(f"Live trading kill switch active: {self.kill_switch_path}", severity="error")
        self.refresh_table()

    async def _start_process(self, name: str) -> None:
        snapshot = await self.process_supervisor.start(name)
        self.notify(f"{snapshot.label} {snapshot.status.lower()}.")
        self.refresh_processes()

    async def _stop_process(self, name: str) -> None:
        snapshot = await self.process_supervisor.stop(name)
        self.notify(f"{snapshot.label} {snapshot.status.lower()}.")
        self.refresh_processes()

    def refresh_processes(self) -> None:
        process_table = self.query_one("#process-table", DataTable)
        process_table.clear()
        snapshots = {snapshot.name: snapshot for snapshot in self.process_supervisor.snapshots()}
        for snapshot in snapshots.values():
            process_table.add_row(
                snapshot.label,
                _status_text(snapshot.status),
                str(snapshot.pid or ""),
                _fmt_uptime(snapshot.uptime_seconds),
                "" if snapshot.exit_code is None else str(snapshot.exit_code),
                str(snapshot.restart_count),
                snapshot.latest_log[:100],
            )

        self.query_one("#start-research", Button).disabled = snapshots["research"].status == "RUNNING"
        self.query_one("#stop-research", Button).disabled = snapshots["research"].status != "RUNNING"
        self.query_one("#start-live", Button).disabled = snapshots["live"].status == "RUNNING"
        self.query_one("#stop-live", Button).disabled = snapshots["live"].status != "RUNNING"
        self._refresh_process_log("research", "#research-log")
        self._refresh_process_log("live", "#live-log")

    def _refresh_process_log(self, name: str, table_id: str) -> None:
        table = self.query_one(table_id, DataTable)
        table.clear()
        for line in self.process_supervisor.logs(name)[-200:]:
            table.add_row(line[:240])

    def refresh_table(self) -> None:
        store = ExecutionStore(self.db_path)
        try:
            signals = store.recent_signals(limit=200)
            decisions = store.recent_decisions(limit=50)
            engine_states = store.recent_engine_states(limit=20)
            live_rows = store.live_dashboard_positions(limit=1000, market_date=self.target_date)
            live_orders = store.recent_live_order_attempts(limit=100)
            live_events = store.recent_live_trade_events(limit=100)
            live_risk = store.recent_live_risk_snapshots(limit=5)
            strategies = store.live_strategies(active_only=False)
            overview = store.research_status_overview(self.target_date)
        finally:
            store.close()
        if self.actionable_only:
            signals = [signal for signal in signals if signal.get("signal_side") != "SKIP"]

        view = _build_live_policy_view(live_rows)
        kill_active = self.kill_switch_path.exists()
        latest_order = live_orders[0] if live_orders else {}
        latest_event = live_events[0] if live_events else {}
        latest_risk = live_risk[0] if live_risk else {}
        latest_book_age = _age_minutes(overview.get("latest_book_ts"))
        marked = sum(1 for row in live_rows if row.get("current_bid") is not None)
        filled = [row for row in live_rows if float(row.get("filled_shares") or 0.0) > 0]
        total_cost = sum(float(row.get("cost_usd") or 0.0) for row in live_rows)
        total_target = sum(float(row.get("target_notional_usd") or 0.0) for row in live_rows)
        total_mtm = sum(float(row.get("unrealized_pnl") or 0.0) for row in live_rows)
        live_rr = total_mtm / total_cost if total_cost else None
        rejected = sum(1 for row in live_rows if str(row.get("state")) == "REJECTED")
        live_strategy_names = {str(row.get("strategy_name")) for row in live_rows}
        active_strategy_count = sum(1 for row in strategies if int(row.get("active") or 0) == 1)

        kill_status = self.query_one("#kill-status", Static)
        kill_status.update(
            f"{'KILL SWITCH ACTIVE' if kill_active else 'Trading enabled'} | stop file {self.kill_switch_path} | Ctrl+K or button creates the stop file"
        )

        live_summary_table = self.query_one("#live-summary", DataTable)
        live_summary_table.clear()
        summary_rows = [
            ("kill switch", "ACTIVE" if kill_active else "clear", str(self.kill_switch_path)),
            ("market date", self.target_date, "live position filter"),
            ("open positions", str(len(live_rows)), f"{len(filled)} with fills, {rejected} rejected"),
            ("risk at work", _fmt_money(total_cost), f"target {_fmt_money(total_target)}"),
            ("live mtm", _fmt_money(total_mtm), f"live R/R {_fmt_pct(live_rr)}"),
            ("quoted positions", f"{marked}/{len(live_rows)}", f"latest book age {'n/a' if latest_book_age is None else f'{latest_book_age:.1f}m'}"),
            ("strategies", str(active_strategy_count), f"{len(live_strategy_names)} have positions today"),
            ("order attempts", str(len(live_orders)), f"last {latest_order.get('final_state', '')} {latest_order.get('final_reason', '')}"),
            ("last event", str(latest_event.get("event_type", "")), str(latest_event.get("message", ""))[:80]),
            ("latest risk snapshot", str(latest_risk.get("timestamp", ""))[:19], _fmt_money(latest_risk.get("open_risk_usd"))),
        ]
        for metric, value, detail in summary_rows:
            live_summary_table.add_row(metric, value, detail)

        strategy_table = self.query_one("#live-strategies", DataTable)
        strategy_table.clear()
        strategy_rows = sorted(
            view["policy_rows"],
            key=lambda row: (_status_priority(str(row.get("status", ""))), -(row.get("risk") or 0.0), row.get("policy", "")),
        )
        for row in strategy_rows:
            strategy_table.add_row(
                _status_text(row.get("status")),
                _status_text(row.get("book_status")),
                str(row.get("policy", "")),
                str(row.get("open_positions", 0)),
                _fmt_money(row.get("risk")),
                _money_text(row.get("mtm")),
                _fmt(row.get("avg_entry")),
                _fmt(row.get("avg_fair")),
                _fmt(row.get("avg_edge")),
                _fmt(row.get("avg_bid")),
                _fmt_pct(row.get("mark_pct")),
                _fmt_pct(row.get("expected_rr")),
                _fmt_pct(row.get("live_rr")),
            )

        station_table = self.query_one("#live-stations", DataTable)
        station_table.clear()
        for row in sorted(view["station_rows"], key=lambda item: (item.get("raw_mtm") or 0.0, item.get("station", "")), reverse=True):
            station_table.add_row(
                str(row.get("station", "")),
                _status_text(row.get("status")),
                str(row.get("raw_count", 0)),
                _fmt_money(row.get("risk")),
                _money_text(row.get("raw_mtm")),
                _fmt_pct(row.get("live_rr")),
                _status_text(row.get("book_status")),
                _fmt_pct(row.get("mark_pct")),
                str(row.get("buy_yes", 0)),
                str(row.get("buy_no", 0)),
            )

        position_rows_by_key = {
            (str(row.get("strategy_name")), str(row.get("station")), str(row.get("market_date")), str(row.get("selected_side")), str(row.get("selected_bucket"))): row
            for row in live_rows
        }
        positions_table = self.query_one("#live-positions", DataTable)
        positions_table.clear()
        for row in view["position_rows"]:
            raw = position_rows_by_key.get(
                (str(row.get("policy")), str(row.get("station")), str(row.get("market_date")), str(row.get("side")), str(row.get("bucket"))),
                {},
            )
            positions_table.add_row(
                str(row.get("time", "")),
                _status_text(raw.get("state", "")),
                str(row.get("policy", "")),
                str(row.get("station", "")),
                str(row.get("market_date", "")),
                str(row.get("side", "")),
                str(row.get("bucket", "")),
                _fmt_money(raw.get("target_notional_usd")),
                _fmt(raw.get("filled_shares")),
                _fmt_money(raw.get("cost_usd")),
                _fmt(row.get("entry")),
                _fmt(row.get("bid")),
                _money_text(raw.get("unrealized_pnl")),
                _fmt_pct(row.get("live_rr")),
                _status_text(row.get("weather_status")),
            )

        orders_table = self.query_one("#live-orders", DataTable)
        orders_table.clear()
        for order in live_orders:
            orders_table.add_row(
                str(order.get("timestamp", ""))[11:19],
                str(order.get("strategy_name", "")),
                str(order.get("station", "")),
                str(order.get("side", "")),
                str(order.get("order_mode", "")),
                _fmt(order.get("limit_price")),
                _fmt_money(order.get("target_notional_usd")),
                _fmt(order.get("filled_shares")),
                _fmt(order.get("avg_price")),
                str(order.get("final_state", "")),
                str(order.get("final_reason", ""))[:30],
                str(order.get("external_order_id", ""))[:18],
            )

        events_table = self.query_one("#live-events", DataTable)
        events_table.clear()
        for event in live_events:
            events_table.add_row(
                str(event.get("timestamp", ""))[11:19],
                str(event.get("strategy_name", "")),
                str(event.get("event_type", "")),
                str(event.get("message", ""))[:100],
                str(event.get("live_position_id", "") or ""),
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
            best_edge = max(edge_yes if edge_yes is not None else float("-inf"), edge_no if edge_no is not None else float("-inf"))
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

        for table_id in OVERVIEW_TABLE_IDS:
            self._apply_table_sort(self.query_one(f"#{table_id}", DataTable))
        summary = self.query_one("#summary", Static)
        summary.update(
            " | ".join(
                [
                    f"{'STOPPED' if kill_active else 'LIVE'}",
                    f"date {self.target_date}",
                    f"positions {len(live_rows)} filled {len(filled)} rejected {rejected}",
                    f"risk {_fmt_money(total_cost)} target {_fmt_money(total_target)} mtm {_fmt_money(total_mtm)} R/R {_fmt_pct(live_rr)}",
                    f"quoted {marked}/{len(live_rows)}",
                    f"orders {len(live_orders)} events {len(live_events)}",
                    f"actionable_only={self.actionable_only}",
                ]
            )
        )
