from __future__ import annotations

import asyncio
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Static, TabbedContent, TabPane

from weather_trader.execution.clob_executor import ClobExecutor
from weather_trader.execution.store import ExecutionStore
from weather_trader.live.resolution import LiveResolutionService
from weather_trader.live.settings import decrypt_age_keyfile_with_passphrase, load_live_settings
from weather_trader.live.sizing import CORE_POLICY_MULTIPLIER, CONSENSUS_POLICY_MULTIPLIER, MOONSHOT_FIXED_NOTIONAL_USD
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


OVERVIEW_TABLE_IDS = {"live-summary", "live-strategies", "live-stations", "live-contracts", "live-positions", "live-performance-table"}
REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_RESOLUTION_POLL_INTERVAL_SECONDS = 6 * 60 * 60

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



def _position_raw(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("position_raw_json") if "position_raw_json" in row else row.get("raw_json")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


def _sizing(row: dict[str, Any]) -> dict[str, Any]:
    raw = _position_raw(row)
    sizing = raw.get("sizing")
    if not isinstance(sizing, dict) and isinstance(raw.get("raw_json"), dict):
        sizing = raw["raw_json"].get("sizing")
    return sizing if isinstance(sizing, dict) else {}


def _sizing_value(row: dict[str, Any], key: str) -> Any:
    return _sizing(row).get(key)


def _sizing_cap(row: dict[str, Any]) -> str:
    sizing = _sizing(row)
    blocked = sizing.get("blocked_reason")
    if blocked:
        return str(blocked)
    target = float(sizing.get("final_target_notional_usd") or row.get("target_notional_usd") or 0.0)
    caps = sizing.get("caps") if isinstance(sizing.get("caps"), dict) else {}
    for name, cap in caps.items():
        if not isinstance(cap, dict):
            continue
        applied = float(cap.get("applied_usd") or 0.0)
        pre_cap = float(sizing.get("pre_cap_target_usd") or 0.0)
        if applied <= target and applied < pre_cap:
            return str(name)
    return "NONE"


def _sizing_multiplier(row: dict[str, Any]) -> str:
    sizing = _sizing(row)
    if not sizing:
        return ""
    policy = sizing.get("policy_multiplier")
    price = sizing.get("price_multiplier")
    if policy is None or price is None:
        return ""
    return f"p{float(policy):.2f} x px{float(price):.2f}"


def _nice_step(raw_step: float) -> float:
    if raw_step <= 0:
        return 1.0
    magnitude = 10 ** math.floor(math.log10(raw_step))
    normalized = raw_step / magnitude
    if normalized <= 1:
        nice = 1
    elif normalized <= 2:
        nice = 2
    elif normalized <= 5:
        nice = 5
    else:
        nice = 10
    return nice * magnitude


def _chart_bounds(values: list[float], ticks: int = 5) -> tuple[float, float, float]:
    if not values:
        return 0.0, 1.0, 1.0
    low = min(min(values), 0.0)
    high = max(max(values), 0.0)
    if low == high:
        high = low + 1.0
    step = _nice_step((high - low) / max(1, ticks - 1))
    low = math.floor(low / step) * step
    high = math.ceil(high / step) * step
    if low == high:
        high += step
    return low, high, step


def _fmt_axis_money(value: float) -> str:
    if abs(value) >= 1000:
        return f"${value / 1000:.1f}k"
    return f"${value:.0f}"


def _render_cumulative_performance(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No live performance yet."
    visible_rows = rows[-60:]
    values = [float(row.get("cumulative_pnl") or 0.0) for row in visible_rows]
    y_min, y_max, step = _chart_bounds(values)
    chart_height = 12
    chart_width = max(12, min(72, len(visible_rows) * 3))
    canvas = [[" " for _ in range(chart_width)] for _ in range(chart_height)]
    span = y_max - y_min
    if y_min <= 0 <= y_max:
        zero_y = round((y_max - 0.0) / span * (chart_height - 1))
        for x in range(chart_width):
            canvas[zero_y][x] = "-"
    for index, value in enumerate(values):
        x = round(index * (chart_width - 1) / max(1, len(values) - 1))
        y = round((y_max - value) / span * (chart_height - 1))
        canvas[y][x] = "*"
    tick_values = []
    current = y_max
    while current >= y_min - (step / 2):
        tick_values.append(round(current, 10))
        current -= step
    tick_labels = {
        round((y_max - tick) / span * (chart_height - 1)): _fmt_axis_money(tick)
        for tick in tick_values
    }
    lines = ["Cumulative PnL by day"]
    for y, canvas_row in enumerate(canvas):
        label = tick_labels.get(y, "")
        lines.append(f"{label:>7} | {''.join(canvas_row)}")
    first_label = str(visible_rows[0].get("utc_date", ""))[5:]
    last_label = str(visible_rows[-1].get("utc_date", ""))[5:]
    x_axis = f"{'':>7} + {'-' * chart_width}"
    label_line = f"{'Days':>7}   {first_label:<{chart_width - len(last_label)}}{last_label}"
    last_value = values[-1]
    return "\n".join([
        *lines,
        x_axis,
        label_line,
        f"{'':>7}   last {last_value:+.2f} | scale {_fmt_axis_money(y_min)} to {_fmt_axis_money(y_max)}",
    ])


def _render_daily_bar_chart(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No live performance yet."
    max_abs = max(abs(float(row.get("daily_pnl") or 0.0)) for row in rows) or 1.0
    scale_width = 34
    lines = ["Last 7 days by day"]
    for row in rows:
        value = float(row.get("daily_pnl") or 0.0)
        width = int(round(abs(value) / max_abs * scale_width)) if value else 0
        width = max(1, width) if value else 0
        bar = "#" * width
        if value < 0:
            bar = f"{bar:>{scale_width}}"
        else:
            bar = f"{bar:<{scale_width}}"
        day = str(row.get("utc_date", ""))[5:] or str(row.get("utc_date", ""))
        lines.append(f"{day:<5} | {bar} {value:+.2f}")
    return "\n".join(lines)


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


def _live_start_label(env: dict[str, str]) -> str:
    return "Start Live Run" if env.get("LIVE_MODE", "dry-run").strip().lower() == "live" else "Start Live Dry Run"


def _is_live_mode(env: dict[str, str]) -> bool:
    return env.get("LIVE_MODE", "dry-run").strip().lower() == "live"


def _registered_strategy_row(strategy: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "ACTIVE" if int(strategy.get("active") or 0) == 1 else "STOPPED",
        "book_status": "NO_POSITIONS",
        "policy": str(strategy.get("name", "")),
        "model_group": str(strategy.get("model_group", "")),
        "strategy_bucket": str(strategy.get("strategy_bucket", "")),
        "obs_delay_bucket": "",
        "open_positions": 0,
        "risk": 0.0,
        "mtm": 0.0,
        "avg_entry": strategy.get("entry_price_min"),
        "avg_fair": None,
        "avg_edge": None,
        "avg_bid": None,
        "mark_pct": None,
        "expected_rr": None,
        "live_rr": None,
    }


class PassphraseScreen(ModalScreen[str | None]):
    BINDINGS = [
        ("enter", "submit", "Submit"),
        ("escape", "cancel", "Cancel"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="passphrase-dialog"):
            yield Static("Unlock Polymarket key", id="passphrase-title")
            yield Input(placeholder="age keyfile passphrase", password=True, id="passphrase-input")
            with Horizontal(id="passphrase-buttons"):
                yield Button("Unlock", id="passphrase-submit", variant="success")
                yield Button("Cancel", id="passphrase-cancel", variant="default")

    def on_mount(self) -> None:
        self.query_one("#passphrase-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "passphrase-cancel":
            self.action_cancel()
            return
        self.action_submit()

    def action_submit(self) -> None:
        self.dismiss(self.query_one("#passphrase-input", Input).value)

    def action_cancel(self) -> None:
        self.dismiss(None)


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
        height: 10;
    }

    #live-stations {
        height: 8;
    }

    #live-contracts {
        height: 10;
    }

    #live-performance-line {
        height: 18;
    }

    #live-performance-bars {
        height: 10;
    }

    #live-performance-table {
        height: 1fr;
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

    PassphraseScreen {
        align: center middle;
    }

    #passphrase-dialog {
        width: 64;
        height: 9;
        padding: 1 2;
        background: #22272b;
        border: tall #f8e6b0;
    }

    #passphrase-title {
        height: 1;
        color: #f8e6b0;
    }

    #passphrase-input {
        margin-top: 1;
    }

    #passphrase-buttons {
        height: 3;
        margin-top: 1;
    }

    #passphrase-buttons Button {
        margin-right: 1;
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
        self._process_log_rows: dict[str, tuple[str, ...]] = {}
        self._process_actions_in_progress: set[str] = set()
        self._last_live_resolution_poll_at: datetime | None = None
        self._last_live_resolution_summary: dict[str, Any] | None = None
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
                    yield Static("Stations", classes="section-title")
                    yield DataTable(id="live-stations")
                    yield Static("Live Positions", classes="section-title")
                    yield DataTable(id="live-contracts")
                    yield Static("Open Exposure", classes="section-title")
                    yield DataTable(id="live-positions")
                with TabPane("Performance", id="performance-tab"):
                    yield Static("Cumulative PnL", classes="section-title")
                    yield Static(id="live-performance-line")
                    yield Static("Daily PnL", classes="section-title")
                    yield Static(id="live-performance-bars")
                    yield Static("Last 7 Days", classes="section-title")
                    yield DataTable(id="live-performance-table")
                with TabPane("Orders", id="orders-tab"):
                    yield Static("Live Order Attempts", classes="section-title")
                    yield DataTable(id="live-orders")
                    yield Static("Live Trade Events", classes="section-title")
                    yield DataTable(id="live-events")
                with TabPane("Config", id="config-tab"):
                    yield Static("Effective Live Config", classes="section-title")
                    yield DataTable(id="live-config")
                with TabPane("Processes", id="processes-tab"):
                    yield Static("Process Supervisor", classes="section-title")
                    with Horizontal(classes="process-controls"):
                        yield Button("Start Research", id="start-research", variant="success")
                        yield Button("Stop Research", id="stop-research", variant="warning")
                        yield Button(_live_start_label(self.process_supervisor.env), id="start-live", variant="success")
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

        live_contracts = self.query_one("#live-contracts", DataTable)
        live_contracts.cursor_type = "row"
        _add_keyed_columns(
            live_contracts,
            [
                ("state", "state"),
                ("station", "station"),
                ("date", "date"),
                ("side", "side"),
                ("bucket", "bucket"),
                ("legs", "legs"),
                ("target", "target"),
                ("cost", "cost"),
                ("filled", "filled"),
                ("entry", "entry"),
                ("bid", "bid"),
                ("pnl", "pnl"),
                ("live R/R", "live_rr"),
                ("weather", "weather"),
                ("high", "high"),
            ],
        )

        live_performance_table = self.query_one("#live-performance-table", DataTable)
        live_performance_table.cursor_type = "row"
        _add_keyed_columns(
            live_performance_table,
            [
                ("date", "utc_date"),
                ("positions", "positions"),
                ("daily pnl", "daily_pnl"),
                ("cumulative pnl", "cumulative_pnl"),
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
                ("base", "base"),
                ("cap", "cap"),
                ("mult", "multiplier"),
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
        live_orders.add_columns("time", "strategy", "station", "side", "mode", "limit", "target", "base", "cap", "mult", "filled", "avg", "state", "reason", "order")

        live_config = self.query_one("#live-config", DataTable)
        live_config.cursor_type = "row"
        live_config.add_columns("group", "setting", "value")

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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "kill-button":
            self.action_activate_kill_switch()
        elif event.button.id == "start-research":
            self._run_process_action("start-research", lambda: self._start_process("research"))
        elif event.button.id == "stop-research":
            self._run_process_action("stop-research", lambda: self._stop_process("research"))
        elif event.button.id == "start-live":
            self._run_process_action("start-live", lambda: self._start_process("live"))
        elif event.button.id == "stop-live":
            self._run_process_action("stop-live", lambda: self._stop_process("live"))

    def _run_process_action(self, action_name: str, action_factory: Callable[[], Awaitable[None]]) -> None:
        if action_name in self._process_actions_in_progress:
            return
        self._process_actions_in_progress.add(action_name)
        self.refresh_processes()

        async def runner() -> None:
            try:
                await action_factory()
            except Exception as exc:
                self.notify(f"Process action failed: {exc}", severity="error")
            finally:
                self._process_actions_in_progress.discard(action_name)
                self.refresh_processes()

        self.run_worker(
            runner(),
            name=action_name,
            group="process-actions",
            exit_on_error=False,
        )

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
        if name == "live" and _is_live_mode(self.process_supervisor.env):
            snapshot = await self._start_live_process_with_key_unlock()
            if snapshot is None:
                self.refresh_processes()
                return
        else:
            snapshot = await self.process_supervisor.start(name)
        self.notify(f"{snapshot.label} {snapshot.status.lower()}.")
        self.refresh_processes()

    async def _start_live_process_with_key_unlock(self):
        if self.process_supervisor.snapshot("live").status == "RUNNING":
            return self.process_supervisor.snapshot("live")
        passphrase = await self._prompt_live_passphrase()
        if not passphrase:
            self.notify("Live start canceled.")
            return None
        read_fd: int | None = None
        private_key = ""
        try:
            private_key = await asyncio.to_thread(self._unlock_and_verify_live_key, passphrase)
            read_fd, write_fd = os.pipe()
            try:
                os.write(write_fd, private_key.encode())
            finally:
                os.close(write_fd)
            snapshot = await self.process_supervisor.start(
                "live",
                extra_env={"POLYMARKET_PRIVATE_KEY_FD": str(read_fd)},
                pass_fds=(read_fd,),
            )
            os.close(read_fd)
            read_fd = None
            return snapshot
        except Exception as exc:
            if read_fd is not None:
                try:
                    os.close(read_fd)
                except OSError:
                    pass
            self.notify(f"Live key unlock/auth failed: {exc}", severity="error")
            return None
        finally:
            passphrase = ""
            private_key = ""

    async def _prompt_live_passphrase(self) -> str | None:
        return await self.push_screen_wait(PassphraseScreen())

    def _unlock_and_verify_live_key(self, passphrase: str) -> str:
        settings = load_live_settings()
        private_key = decrypt_age_keyfile_with_passphrase(settings.polymarket_keyfile_path, passphrase)
        executor = ClobExecutor(private_key=private_key, settings=settings)
        if executor.check_kill_switch():
            raise RuntimeError("kill switch is active")
        allowance = executor.check_allowance_buy(1.0)
        if not allowance.ok:
            raise RuntimeError(f"allowance check failed: {allowance.reason or 'unknown'}")
        return private_key

    async def _stop_process(self, name: str) -> None:
        snapshot = await self.process_supervisor.stop(name)
        self.notify(f"{snapshot.label} {snapshot.status.lower()}.")
        self.refresh_processes()


    def _refresh_config_table(self, settings) -> None:
        table = self.query_one("#live-config", DataTable)
        table.clear()
        rows = [
            ("Execution", "mode", self.process_supervisor.env.get("LIVE_MODE", "dry-run")),
            ("Execution", "CLOB URL", settings.polymarket_clob_url),
            ("Execution", "client version", settings.polymarket_clob_client_version),
            ("Execution", "allowance check", str(settings.live_require_allowance_check)),
            ("Execution", "kill switch path", settings.live_kill_switch_path),
            ("Sizing", "bankroll", _fmt_money(settings.live_bankroll_usd)),
            ("Sizing", "fixed fraction", _fmt_pct(settings.live_fixed_fraction)),
            ("Sizing", "base notional", _fmt_money(settings.live_base_notional_usd)),
            ("Sizing", "min order", _fmt_money(settings.live_min_order_notional)),
            ("Sizing", "max order", _fmt_money(settings.live_max_usd_per_order)),
            ("Risk caps", "total open", _fmt_money(settings.live_max_total_open_risk)),
            ("Risk caps", "daily new", _fmt_money(settings.live_max_daily_new_risk)),
            ("Risk caps", "station/date", _fmt_money(settings.live_max_exposure_per_station_date)),
            ("Risk caps", "station/date/side", _fmt_money(settings.live_max_exposure_per_station_date_side)),
            ("Risk caps", "exact bucket/side", _fmt_money(settings.live_max_exposure_per_exact_bucket_side)),
            ("Strategy tiers", "core multiplier", f"{CORE_POLICY_MULTIPLIER:.2f}x"),
            ("Strategy tiers", "consensus multiplier", f"{CONSENSUS_POLICY_MULTIPLIER:.2f}x"),
            ("Strategy tiers", "moonshot fixed", _fmt_money(MOONSHOT_FIXED_NOTIONAL_USD)),
            ("Price bands", "< 0.10", "0.25x except moonshot"),
            ("Price bands", "0.10-0.25", "0.60x"),
            ("Price bands", "0.25-0.75", "1.00x"),
            ("Price bands", "> 0.75", "0.60x"),
        ]
        for group, setting, value in rows:
            table.add_row(group, setting, str(value))

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

        self.query_one("#start-research", Button).disabled = (
            snapshots["research"].status == "RUNNING" or "start-research" in self._process_actions_in_progress
        )
        self.query_one("#stop-research", Button).disabled = (
            snapshots["research"].status != "RUNNING" or "stop-research" in self._process_actions_in_progress
        )
        self.query_one("#start-live", Button).disabled = (
            snapshots["live"].status == "RUNNING" or "start-live" in self._process_actions_in_progress
        )
        self.query_one("#stop-live", Button).disabled = (
            snapshots["live"].status != "RUNNING" or "stop-live" in self._process_actions_in_progress
        )
        self._refresh_process_log("research", "#research-log")
        self._refresh_process_log("live", "#live-log")

    def _refresh_process_log(self, name: str, table_id: str) -> None:
        table = self.query_one(table_id, DataTable)
        rows = tuple(self.process_supervisor.logs(name)[-200:])
        if self._process_log_rows.get(name) == rows:
            return
        self._process_log_rows[name] = rows
        scroll_x = table.scroll_x
        scroll_y = table.scroll_y
        follow_tail = table.is_vertical_scroll_end and not table.is_vertical_scrollbar_grabbed
        table.clear()
        for line in rows:
            table.add_row(line[:240])
        if follow_tail:
            table.scroll_end(animate=False, immediate=True)
        else:
            table.scroll_to(x=scroll_x, y=scroll_y, animate=False, force=True, immediate=True)

    def refresh_table(self) -> None:
        store = ExecutionStore(self.db_path)
        try:
            self._refresh_target_date(store)
            self._maybe_resolve_live_positions(store)
            signals = store.recent_signals(limit=200)
            decisions = store.recent_decisions(limit=50)
            engine_states = store.recent_engine_states(limit=20)
            live_rows = store.live_dashboard_positions(limit=1000, market_date=self.target_date)
            live_orders = store.recent_live_order_attempts(limit=100)
            live_events = store.recent_live_trade_events(limit=100)
            live_risk = store.recent_live_risk_snapshots(limit=5)
            exposure = store.live_exposure_summary()
            performance = store.live_performance_summary()
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
        resting_orders = []
        for order in live_orders:
            try:
                payload = json.loads(str(order.get("raw_payload") or "{}"))
            except Exception:
                payload = {}
            execution = payload.get("execution") if isinstance(payload, dict) else {}
            if str(order.get("order_mode", "")) == "GTC" and execution.get("attempt_label") == "resting_fallback":
                resting_orders.append(order)
        resting_filled = sum(1 for order in resting_orders if str(order.get("final_state", "")) == "FILLED")
        resting_partial = sum(1 for order in resting_orders if str(order.get("final_state", "")) == "PARTIAL")
        resting_incomplete = len(resting_orders) - resting_filled

        settings = load_live_settings()
        daily_key = datetime.now(timezone.utc).date().isoformat()
        open_risk = float(exposure.get("open_risk_usd") or 0.0)
        daily_new = float((exposure.get("daily_new_risk_usd") or {}).get(daily_key, 0.0))
        station_exposures = exposure.get("station_date_exposure_usd") or {}
        largest_station_date = max(station_exposures.items(), key=lambda item: item[1], default=("n/a", 0.0))
        registered_policy_names = {str(strategy.get("name", "")) for strategy in strategies}
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
            self._live_resolution_summary_row(),
            ("open positions", str(len(live_rows)), f"{len(filled)} with fills, {rejected} rejected"),
            ("risk at work", _fmt_money(total_cost), f"target {_fmt_money(total_target)}"),
            ("open risk", f"{_fmt_money(open_risk)} / {_fmt_money(settings.live_max_total_open_risk)}", "active reserved/submitted/filled risk"),
            ("daily new risk", f"{_fmt_money(daily_new)} / {_fmt_money(settings.live_max_daily_new_risk)}", f"UTC {daily_key}"),
            ("largest station/date", f"{_fmt_money(largest_station_date[1])} / {_fmt_money(settings.live_max_exposure_per_station_date)}", str(largest_station_date[0])),
            ("live mtm", _fmt_money(total_mtm), f"live R/R {_fmt_pct(live_rr)}"),
            ("quoted positions", f"{marked}/{len(live_rows)}", f"latest book age {'n/a' if latest_book_age is None else f'{latest_book_age:.1f}m'}"),
            ("strategies", str(active_strategy_count), f"{len(live_strategy_names & registered_policy_names)} have positions today"),
            ("order attempts", str(len(live_orders)), f"last {latest_order.get('final_state', '')} {latest_order.get('final_reason', '')}"),
            ("resting fallback", str(len(resting_orders)), f"{resting_filled} filled, {resting_incomplete} incomplete after 120s ({resting_partial} partial)"),

            ("last event", str(latest_event.get("event_type", "")), str(latest_event.get("message", ""))[:80]),
            ("latest risk snapshot", str(latest_risk.get("timestamp", ""))[:19], _fmt_money(latest_risk.get("open_risk_usd"))),
        ]
        for metric, value, detail in summary_rows:
            live_summary_table.add_row(metric, value, detail)

        self._refresh_config_table(settings)

        strategy_table = self.query_one("#live-strategies", DataTable)
        strategy_table.clear()
        strategy_rows_by_policy = {str(row.get("policy", "")): row for row in view["policy_rows"]}
        for strategy in strategies:
            name = str(strategy.get("name", ""))
            if name not in strategy_rows_by_policy:
                strategy_rows_by_policy[name] = _registered_strategy_row(strategy)
        strategy_rows = sorted(
            strategy_rows_by_policy.values(),
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

        contracts_table = self.query_one("#live-contracts", DataTable)
        contracts_table.clear()
        for row in view["exposure_rows"]:
            contracts_table.add_row(
                _status_text(row.get("status")),
                str(row.get("station", "")),
                str(row.get("market_date", "")),
                str(row.get("side", "")),
                str(row.get("bucket", "")),
                str(row.get("rows", 0)),
                _fmt_money(row.get("target")),
                _fmt_money(row.get("cost")),
                _fmt(row.get("shares")),
                _fmt(row.get("entry")),
                _fmt(row.get("mark")),
                _money_text(row.get("pnl")),
                _fmt_pct(row.get("live_rr")),
                _status_text(row.get("weather_status")),
                _fmt(row.get("weather_high")),
            )

        performance_line = self.query_one("#live-performance-line", Static)
        performance_line.update(_render_cumulative_performance(performance.get("daily_rows", [])))

        performance_bars = self.query_one("#live-performance-bars", Static)
        performance_bars.update(_render_daily_bar_chart(performance.get("last_7_days", [])))

        performance_table = self.query_one("#live-performance-table", DataTable)
        performance_table.clear()
        for row in performance.get("last_7_days", []):
            performance_table.add_row(
                str(row.get("utc_date", "")),
                str(row.get("positions", 0)),
                _money_text(row.get("daily_pnl")),
                _money_text(row.get("cumulative_pnl")),
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
                _fmt_money(_sizing_value(raw, "base_notional_usd")),
                _sizing_cap(raw),
                _sizing_multiplier(raw),
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
                _fmt_money(_sizing_value(order, "base_notional_usd")),
                _sizing_cap(order),
                _sizing_multiplier(order),
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

    def _refresh_target_date(self, store: ExecutionStore) -> None:
        latest = store.latest_live_market_date()
        if latest is None:
            return
        if str(latest) > str(self.target_date):
            self.target_date = str(latest)

    def _maybe_resolve_live_positions(self, store: ExecutionStore) -> None:
        now = datetime.now(timezone.utc)
        if (
            self._last_live_resolution_poll_at is not None
            and (now - self._last_live_resolution_poll_at).total_seconds() < LIVE_RESOLUTION_POLL_INTERVAL_SECONDS
        ):
            return
        self._last_live_resolution_poll_at = now
        try:
            summary = LiveResolutionService(store).resolve_due(as_of_utc=now)
        except Exception as exc:
            self._last_live_resolution_summary = {
                "resolved": 0,
                "pending": 0,
                "errors": 1,
                "timestamp": now,
                "detail": str(exc)[:80],
            }
            return
        self._last_live_resolution_summary = {
            "resolved": int(summary.resolved),
            "pending": int(summary.pending),
            "errors": len(summary.errors),
            "timestamp": now,
            "detail": f"{summary.candidates} candidates, {summary.skipped} skipped",
        }

    def _live_resolution_summary_row(self) -> tuple[str, str, str]:
        summary = self._last_live_resolution_summary
        if summary is None:
            return ("live resolution", "not polled", "")
        timestamp = summary.get("timestamp")
        last = timestamp.astimezone().strftime("%H:%M:%S") if isinstance(timestamp, datetime) else str(timestamp or "")
        return (
            "live resolution",
            f"resolved {summary.get('resolved', 0)} pending {summary.get('pending', 0)} errors {summary.get('errors', 0)}",
            f"last {last} {summary.get('detail', '')}".strip(),
        )
