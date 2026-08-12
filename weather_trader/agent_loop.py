from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from weather_trader.learning_loop import (
    LearningLoopError,
    capture_learning,
    check_learning,
    learning_counts,
    revisit_learning,
    write_learning_index,
)


OPEN_STATUSES = {"ACTIVE", "PARKED", "WAITING"}
ALL_STATUSES = OPEN_STATUSES | {"CLOSED"}
MAX_OPEN_THREADS = 7
MAX_ACTIVE_THREADS = 3
STATE_WORD_LIMIT = 1500
EDGE_PILLARS = (
    "information",
    "settlement",
    "execution",
    "costs-adverse-selection",
    "cross-pillar",
)
EDGE_PILLAR_LABELS = {
    "information": "Information",
    "settlement": "Settlement",
    "execution": "Execution",
    "costs-adverse-selection": "Costs & adverse selection",
    "cross-pillar": "Cross-pillar",
}
REQUIRED_SECTIONS = (
    "Question",
    "Current Answer",
    "Evidence",
    "Next Action",
    "Closure Output",
)
DATE_RE = re.compile(r"^(?:Last reviewed|Last updated):\s*(\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)
THREAD_RE = re.compile(r"^T(\d{4})-[a-z0-9][a-z0-9-]*\.md$")


class AgentLoopError(RuntimeError):
    pass


@dataclass(frozen=True)
class ThreadRecord:
    path: Path
    metadata: dict[str, str]
    body: str

    @property
    def thread_id(self) -> str:
        return self.metadata["id"]

    @property
    def status(self) -> str:
        return self.metadata["status"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_timestamp(now: datetime | None = None) -> str:
    return (now or utc_now()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today(now: datetime | None = None) -> str:
    return (now or utc_now()).date().isoformat()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:64].rstrip("-") or "work-thread"


def _run(command: Sequence[str], cwd: Path, timeout: float = 5.0) -> dict[str, object]:
    try:
        result = subprocess.run(
            list(command),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "error": str(exc)}
    if result.returncode != 0:
        return {
            "available": False,
            "returncode": result.returncode,
            "error": (result.stderr or result.stdout).strip()[:500],
        }
    return {"available": True, "value": result.stdout.strip()}


def _service_state(unit: str, cwd: Path) -> dict[str, object]:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", unit],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "error": str(exc)}
    state = (result.stdout or result.stderr).strip()
    return {"available": True, "value": state, "returncode": result.returncode}


def _sqlite_value(path: Path, sql: str) -> object:
    uri = f"file:{path}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=2.0) as connection:
        row = connection.execute(sql).fetchone()
    return row[0] if row else None


def _sqlite_facts(path: Path, queries: dict[str, str]) -> dict[str, object]:
    result: dict[str, object] = {
        "path": str(path),
        "available": path.exists(),
    }
    if not path.exists():
        return result
    stat = path.stat()
    result.update({"size_bytes": stat.st_size, "modified_at": utc_timestamp(datetime.fromtimestamp(stat.st_mtime, timezone.utc))})
    errors: dict[str, str] = {}
    for name, sql in queries.items():
        try:
            result[name] = _sqlite_value(path, sql)
        except (sqlite3.Error, OSError) as exc:
            errors[name] = str(exc)
    if errors:
        result["query_errors"] = errors
    return result


def parse_thread(path: Path) -> ThreadRecord:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise AgentLoopError(f"{path}: missing metadata block")
    try:
        raw_metadata, body = text[4:].split("\n---\n", 1)
    except ValueError as exc:
        raise AgentLoopError(f"{path}: unterminated metadata block") from exc
    metadata: dict[str, str] = {}
    for line in raw_metadata.splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise AgentLoopError(f"{path}: invalid metadata line: {line}")
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()
    required = {"id", "title", "status", "priority", "owner", "opened", "last_touched", "facts_fingerprint"}
    missing = sorted(required - metadata.keys())
    if missing:
        raise AgentLoopError(f"{path}: missing metadata: {', '.join(missing)}")
    if metadata["status"] not in ALL_STATUSES:
        raise AgentLoopError(f"{path}: invalid status {metadata['status']}")
    if "pillar" in metadata and metadata["pillar"] not in EDGE_PILLARS:
        raise AgentLoopError(f"{path}: invalid pillar {metadata['pillar']}")
    return ThreadRecord(path=path, metadata=metadata, body=body)


def render_thread(metadata: dict[str, str], body: str) -> str:
    ordered = (
        "id",
        "title",
        "status",
        "pillar",
        "priority",
        "owner",
        "opened",
        "last_touched",
        "facts_fingerprint",
        "closed",
    )
    lines = ["---"]
    for key in ordered:
        if key in metadata:
            lines.append(f"{key}: {metadata[key]}")
    lines.extend(("---", "", body.strip(), ""))
    return "\n".join(lines)


def section_map(body: str) -> dict[str, str]:
    matches = list(re.finditer(r"^## ([^\n]+)\s*$", body, re.MULTILINE))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[match.group(1).strip()] = body[match.end() : end].strip()
    return sections


def discover_threads(root: Path) -> list[ThreadRecord]:
    board = root / "board"
    paths = list(board.glob("T????-*.md")) + list((board / "closed").glob("*/*.md"))
    records = [parse_thread(path) for path in sorted(paths)]
    seen: set[str] = set()
    for record in records:
        if record.thread_id in seen:
            raise AgentLoopError(f"duplicate thread id: {record.thread_id}")
        seen.add(record.thread_id)
    return records


def fact_fingerprint(facts: dict[str, object]) -> str:
    payload = json.dumps(facts, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def collect_facts(root: Path, now: datetime | None = None) -> dict[str, object]:
    now = now or utc_now()
    warnings: list[str] = []
    git_head = _run(("git", "rev-parse", "HEAD"), root)
    git_branch = _run(("git", "branch", "--show-current"), root)
    docs: dict[str, object] = {}
    doc_paths = {
        "state": root / "agent_loop" / "STATE.md",
        "audit": root / "docs" / "current-trading-system-audit.md",
        "roadmap": root / "docs" / "execution-rebuild-roadmap.md",
        "live_journal": root / "docs" / "live-trading-journal.md",
        "project_overview": root / "docs" / "project-overview.md",
        "improvement_loop": root / "docs" / "continuous-improvement-loop.md",
    }
    dated_docs: dict[str, str] = {}
    for name, path in doc_paths.items():
        item: dict[str, object] = {"path": str(path.relative_to(root)), "available": path.exists()}
        if path.exists():
            text = path.read_text(encoding="utf-8")
            match = DATE_RE.search(text)
            if match:
                item["review_date"] = match.group(1)
                dated_docs[name] = match.group(1)
            elif name != "state":
                warnings.append(f"{name} has no machine-readable Last reviewed/Last updated date")
            item["word_count"] = len(text.split())
        docs[name] = item
    if dated_docs:
        newest = dated_docs.get("audit", max(dated_docs.values()))
        for name, review_date in dated_docs.items():
            if name not in {"state", "audit"} and review_date < newest:
                warnings.append(f"{name} review date {review_date} predates audit review {newest}")

    try:
        records = discover_threads(root)
    except AgentLoopError as exc:
        records = []
        warnings.append(str(exc))
    counts = {status.lower(): sum(record.status == status for record in records) for status in ALL_STATUSES}
    counts["open"] = sum(record.status in OPEN_STATUSES for record in records)
    counts["pillars"] = {
        pillar: sum(record.status in OPEN_STATUSES and record.metadata.get("pillar") == pillar for record in records)
        for pillar in EDGE_PILLARS
    }

    runtime_root = Path("/home/maxrush/.local/state/roboweather")
    research_db = runtime_root / "research_2026-05-08_multimodel.sqlite"
    discovery_db = runtime_root / "discovery" / "catalog.sqlite"
    runtime = {
        "research_database": _sqlite_facts(
            research_db,
            {
                "snapshot_count": "select count(*) from prediction_snapshots",
                "latest_snapshot_timestamp": "select max(timestamp) from prediction_snapshots",
                "latest_resolved_market_date": "select max(market_date) from station_date_outcomes where final_high_tmpf is not null",
            },
        ),
        "discovery_registry": _sqlite_facts(
            discovery_db,
            {
                "completed_run_count": "select count(*) from discovery_runs where status in ('COMPLETED', 'NO_NOMINATION', 'BUDGET_EXHAUSTED')",
                "candidate_version_count": "select count(*) from candidate_versions",
            },
        ),
        "services": {},
    }
    for unit in (
        "roboweather-research.service",
        "roboweather-market-tape.service",
        "roboweather-phase3d-discovery.service",
    ):
        runtime["services"][unit] = _service_state(unit, root)

    stable_facts: dict[str, object] = {
        "schema_version": 1,
        "repository": {
            "root": str(root),
            "head": git_head,
            "branch": git_branch,
        },
        "documents": docs,
        "board": counts,
        "learning": learning_counts(root),
        "runtime": runtime,
        "warnings": warnings,
    }
    return {
        "generated_at": utc_timestamp(now),
        "facts_fingerprint": fact_fingerprint({key: value for key, value in stable_facts.items() if key != "board"}),
        **stable_facts,
    }


def write_facts(root: Path, now: datetime | None = None) -> dict[str, object]:
    facts = collect_facts(root, now=now)
    path = root / "agent_loop" / "facts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(facts, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return facts


def read_facts(root: Path) -> dict[str, object]:
    path = root / "agent_loop" / "facts.json"
    if not path.exists():
        return write_facts(root)
    return json.loads(path.read_text(encoding="utf-8"))


def render_index(records: Iterable[ThreadRecord], generated_at: str) -> str:
    records = list(records)
    open_records = sorted((record for record in records if record.status in OPEN_STATUSES), key=lambda item: item.thread_id)
    closed_records = sorted((record for record in records if record.status == "CLOSED"), key=lambda item: item.thread_id, reverse=True)
    lines = [
        "# Work Board",
        "",
        "Generated by `scripts/agent_loop.py refresh`. Do not edit this file by hand.",
        "",
        f"Generated at: {generated_at}",
        f"Open: {len(open_records)}/{MAX_OPEN_THREADS} | Active: {sum(record.status == 'ACTIVE' for record in open_records)}/{MAX_ACTIVE_THREADS}",
        "",
        "## Open Threads",
        "",
        "| ID | Pillar | Status | Priority | Question | Next action | Last touched |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    if open_records:
        for record in open_records:
            sections = section_map(record.body)
            question = sections.get("Question", "").replace("\n", " ")
            next_action = sections.get("Next Action", "").replace("\n", " ")
            rel = Path(record.path.name)
            pillar = EDGE_PILLAR_LABELS.get(record.metadata.get("pillar", ""), "Legacy/unassigned")
            lines.append(
                f"| [{record.thread_id}]({rel.as_posix()}) | {pillar} | {record.status} | {record.metadata['priority']} | "
                f"{question} | {next_action} | {record.metadata['last_touched']} |"
            )
    else:
        lines.append("| — | — | — | — | No open threads. | — | — |")
    lines.extend(
        (
            "",
            "## Recently Closed",
            "",
            "| ID | Pillar | Result | Durable output | Closed |",
            "| --- | --- | --- | --- | --- |",
        )
    )
    if closed_records:
        for record in closed_records[:20]:
            sections = section_map(record.body)
            outcome = sections.get("Outcome", "").replace("\n", " ")
            durable = sections.get("Durable Output", "").replace("\n", " ")
            rel = Path("closed") / record.metadata["closed"][:4] / record.path.name
            pillar = EDGE_PILLAR_LABELS.get(record.metadata.get("pillar", ""), "Legacy/unassigned")
            lines.append(
                f"| [{record.thread_id}]({rel.as_posix()}) | {pillar} | {outcome} | {durable} | "
                f"{record.metadata['closed']} |"
            )
    else:
        lines.append("| — | — | — | — | — |")
    lines.extend(
        (
            "",
            "## Rules",
            "",
            f"- Maximum {MAX_OPEN_THREADS} open threads and {MAX_ACTIVE_THREADS} active threads.",
            "- One question, one primary edge pillar, and exactly one next action per thread.",
            "- `cross-pillar` is reserved for integration gates and project governance; it is not a fifth source of edge.",
            "- Use the lifecycle skills or `scripts/agent_loop.py`; never hand-edit this index.",
            "",
        )
    )
    return "\n".join(lines)


def write_index(root: Path, generated_at: str | None = None) -> str:
    records = discover_threads(root)
    content = render_index(records, generated_at or utc_timestamp())
    path = root / "board" / "INDEX.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return content


def refresh(root: Path) -> None:
    facts = write_facts(root)
    write_index(root, str(facts["generated_at"]))
    write_learning_index(root, str(facts["generated_at"]))


def _next_id(records: Iterable[ThreadRecord]) -> str:
    numbers = [int(record.thread_id[1:]) for record in records]
    return f"T{(max(numbers, default=0) + 1):04d}"


def _thread_by_id(root: Path, thread_id: str) -> ThreadRecord:
    normalized = thread_id.upper()
    matches = [record for record in discover_threads(root) if record.thread_id == normalized]
    if not matches:
        raise AgentLoopError(f"unknown thread: {normalized}")
    return matches[0]


def start_thread(root: Path, args: argparse.Namespace) -> Path:
    facts = write_facts(root)
    records = discover_threads(root)
    open_records = [record for record in records if record.status in OPEN_STATUSES]
    active_records = [record for record in records if record.status == "ACTIVE"]
    if len(open_records) >= MAX_OPEN_THREADS:
        raise AgentLoopError(f"open-thread cap reached ({MAX_OPEN_THREADS}); close or consolidate a thread first")
    if args.status == "ACTIVE" and len(active_records) >= MAX_ACTIVE_THREADS:
        raise AgentLoopError(f"active-thread cap reached ({MAX_ACTIVE_THREADS}); park an active thread first")
    thread_id = _next_id(records)
    metadata = {
        "id": thread_id,
        "title": args.title.strip(),
        "status": args.status,
        "pillar": args.pillar,
        "priority": args.priority,
        "owner": args.owner,
        "opened": today(),
        "last_touched": today(),
        "facts_fingerprint": str(facts["facts_fingerprint"]),
    }
    body = f"""# {thread_id} {args.title.strip()}

## Question

{args.question.strip()}

## Current Answer

Not established yet.

## Evidence

- No evidence recorded yet.

## Next Action

{args.next_action.strip()}

## Closure Output

{args.closure_output.strip()}
"""
    path = root / "board" / f"{thread_id}-{slugify(args.title)}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_thread(metadata, body), encoding="utf-8")
    facts = write_facts(root)
    write_index(root, str(facts["generated_at"]))
    return path


def park_thread(root: Path, args: argparse.Namespace) -> Path:
    facts = write_facts(root)
    record = _thread_by_id(root, args.thread_id)
    if record.status == "CLOSED":
        raise AgentLoopError(f"{record.thread_id} is closed")
    sections = section_map(record.body)
    evidence = args.evidence or ["No new evidence recorded."]
    body = f"""# {record.thread_id} {record.metadata['title']}

## Question

{sections['Question']}

## Current Answer

{args.answer.strip()}

## Evidence

{chr(10).join(f'- {item.strip()}' for item in evidence)}

## Next Action

{args.next_action.strip()}

## Closure Output

{sections['Closure Output']}
"""
    metadata = dict(record.metadata)
    metadata.update({"status": args.status, "last_touched": today(), "facts_fingerprint": str(facts["facts_fingerprint"])})
    record.path.write_text(render_thread(metadata, body), encoding="utf-8")
    facts = write_facts(root)
    write_index(root, str(facts["generated_at"]))
    return record.path


def resume_thread(root: Path, args: argparse.Namespace) -> str:
    facts = write_facts(root)
    record = _thread_by_id(root, args.thread_id)
    if record.status == "CLOSED":
        raise AgentLoopError(f"{record.thread_id} is closed; open a new question instead")
    active_records = [item for item in discover_threads(root) if item.status == "ACTIVE" and item.thread_id != record.thread_id]
    if len(active_records) >= MAX_ACTIVE_THREADS:
        raise AgentLoopError(f"active-thread cap reached ({MAX_ACTIVE_THREADS}); park an active thread first")
    previous = record.metadata["facts_fingerprint"]
    current = str(facts["facts_fingerprint"])
    metadata = dict(record.metadata)
    metadata.update({"status": "ACTIVE", "last_touched": today(), "facts_fingerprint": current})
    record.path.write_text(render_thread(metadata, record.body), encoding="utf-8")
    facts = write_facts(root)
    write_index(root, str(facts["generated_at"]))
    changed = "CHANGED" if previous != current else "unchanged"
    sections = section_map(record.body)
    return "\n".join(
        (
            f"Thread: {record.thread_id} — {record.metadata['title']}",
            f"Pillar: {EDGE_PILLAR_LABELS.get(record.metadata.get('pillar', ''), 'Legacy/unassigned')}",
            f"Facts since park: {changed}",
            f"Question: {sections['Question']}",
            f"Current answer: {sections['Current Answer']}",
            f"Next action: {sections['Next Action']}",
            f"Closure output: {sections['Closure Output']}",
        )
    )


def close_thread(root: Path, args: argparse.Namespace) -> Path:
    facts = write_facts(root)
    record = _thread_by_id(root, args.thread_id)
    if record.status == "CLOSED":
        raise AgentLoopError(f"{record.thread_id} is already closed")
    sections = section_map(record.body)
    evidence = args.evidence or [item[2:] for item in sections.get("Evidence", "").splitlines() if item.startswith("- ")]
    if not evidence:
        evidence = ["No additional evidence recorded."]
    closed_date = today()
    body = f"""# {record.thread_id} {record.metadata['title']}

## Question

{sections['Question']}

## Outcome

{args.outcome.strip()}

## Evidence

{chr(10).join(f'- {item.strip()}' for item in evidence)}

## Durable Output

{args.durable_output.strip()}
"""
    metadata = dict(record.metadata)
    metadata.update(
        {
            "status": "CLOSED",
            "last_touched": closed_date,
            "facts_fingerprint": str(facts["facts_fingerprint"]),
            "closed": closed_date,
        }
    )
    destination = root / "board" / "closed" / closed_date[:4] / record.path.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_thread(metadata, body), encoding="utf-8")
    record.path.unlink()
    facts = write_facts(root)
    write_index(root, str(facts["generated_at"]))
    return destination


def check(root: Path) -> list[str]:
    errors: list[str] = []
    state_path = root / "agent_loop" / "STATE.md"
    if not state_path.exists():
        errors.append("agent_loop/STATE.md is missing")
    else:
        word_count = len(state_path.read_text(encoding="utf-8").split())
        if word_count > STATE_WORD_LIMIT:
            errors.append(f"agent_loop/STATE.md has {word_count} words; limit is {STATE_WORD_LIMIT}")
    facts_path = root / "agent_loop" / "facts.json"
    if not facts_path.exists():
        errors.append("agent_loop/facts.json is missing; run refresh")
    else:
        try:
            facts = json.loads(facts_path.read_text(encoding="utf-8"))
            for key in ("generated_at", "facts_fingerprint", "schema_version", "repository", "documents", "board", "learning", "runtime", "warnings"):
                if key not in facts:
                    errors.append(f"agent_loop/facts.json is missing {key}")
        except json.JSONDecodeError as exc:
            errors.append(f"agent_loop/facts.json is invalid JSON: {exc}")
    try:
        records = discover_threads(root)
    except AgentLoopError as exc:
        errors.append(str(exc))
        records = []
    open_records = [record for record in records if record.status in OPEN_STATUSES]
    active_records = [record for record in records if record.status == "ACTIVE"]
    if len(open_records) > MAX_OPEN_THREADS:
        errors.append(f"open-thread cap exceeded: {len(open_records)}/{MAX_OPEN_THREADS}")
    if len(active_records) > MAX_ACTIVE_THREADS:
        errors.append(f"active-thread cap exceeded: {len(active_records)}/{MAX_ACTIVE_THREADS}")
    questions: dict[str, str] = {}
    for record in records:
        expected_name = THREAD_RE.match(record.path.name)
        if not expected_name:
            errors.append(f"{record.path}: invalid thread filename")
        if record.path.name[:5] != record.thread_id:
            errors.append(f"{record.path}: filename/id mismatch")
        sections = section_map(record.body)
        required = REQUIRED_SECTIONS if record.status in OPEN_STATUSES else ("Question", "Outcome", "Evidence", "Durable Output")
        for section in required:
            if not sections.get(section, "").strip():
                errors.append(f"{record.path}: missing or empty section {section}")
        if record.status in OPEN_STATUSES and not record.metadata.get("pillar"):
            errors.append(f"{record.path}: open thread is missing pillar")
        question = re.sub(r"\s+", " ", sections.get("Question", "").strip().lower())
        if record.status in OPEN_STATUSES and question:
            if question in questions:
                errors.append(f"duplicate open question in {record.thread_id} and {questions[question]}")
            questions[question] = record.thread_id
    index_path = root / "board" / "INDEX.md"
    if not index_path.exists():
        errors.append("board/INDEX.md is missing; run refresh")
    errors.extend(check_learning(root))
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Maintain RoboWeather agent-loop state and work threads.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("refresh", help="Regenerate facts.json and board/INDEX.md.")
    subparsers.add_parser("check", help="Validate state, facts, board caps, and thread schema.")
    subparsers.add_parser("stop", help="Refresh generated files and then validate the coordination layer.")

    start = subparsers.add_parser("start-thread", help="Create one bounded work thread.")
    start.add_argument("--title", required=True)
    start.add_argument("--question", required=True)
    start.add_argument("--next-action", required=True)
    start.add_argument("--closure-output", required=True)
    start.add_argument("--pillar", choices=EDGE_PILLARS, required=True)
    start.add_argument("--priority", choices=("critical", "high", "normal", "low"), default="normal")
    start.add_argument("--owner", default="unassigned")
    start.add_argument("--status", choices=("ACTIVE", "PARKED", "WAITING"), default="ACTIVE")

    park = subparsers.add_parser("park-thread", help="Record a resumable handoff.")
    park.add_argument("thread_id")
    park.add_argument("--answer", required=True)
    park.add_argument("--evidence", action="append")
    park.add_argument("--next-action", required=True)
    park.add_argument("--status", choices=("PARKED", "WAITING"), default="PARKED")

    resume = subparsers.add_parser("resume-thread", help="Refresh facts and resume a parked thread.")
    resume.add_argument("thread_id")

    close = subparsers.add_parser("close-thread", help="Close a thread into a named durable output.")
    close.add_argument("thread_id")
    close.add_argument("--outcome", required=True)
    close.add_argument("--durable-output", required=True)
    close.add_argument("--evidence", action="append")

    capture = subparsers.add_parser("capture-learning", help="Preserve a concept, failure lesson, or design intuition.")
    capture.add_argument("--title", required=True)
    capture.add_argument("--kind", choices=("CONCEPT", "FAILURE", "DESIGN", "EXPERIENCE"), required=True)
    capture.add_argument("--origin", default="none")
    capture.add_argument("--why", required=True)
    capture.add_argument("--happened", required=True)
    capture.add_argument("--concept", required=True)
    capture.add_argument("--intuition", required=True)
    capture.add_argument("--pattern", required=True)
    capture.add_argument("--application", required=True)
    capture.add_argument("--questions", required=True)
    capture.add_argument("--evidence", action="append")
    capture.add_argument("--revisit-on", required=True)
    capture.add_argument("--status", choices=("CAPTURED", "REVISIT"), default="CAPTURED")

    revisit = subparsers.add_parser("revisit-learning", help="Append reflection and update a learning card maturity.")
    revisit.add_argument("learning_id")
    revisit.add_argument("--reflection", required=True)
    revisit.add_argument("--connection", required=True)
    revisit.add_argument("--action", required=True)
    revisit.add_argument("--status", choices=("REVISIT", "INTEGRATED", "SUPERSEDED"), required=True)
    revisit.add_argument("--revisit-on", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "refresh":
            refresh(root)
            print("Refreshed agent_loop/facts.json and board/INDEX.md")
        elif args.command == "check":
            errors = check(root)
            if errors:
                for error in errors:
                    print(f"ERROR: {error}")
                return 1
            print("Agent-loop checks passed")
        elif args.command == "stop":
            refresh(root)
            errors = check(root)
            if errors:
                for error in errors:
                    print(f"ERROR: {error}")
                return 1
            print("Agent-loop stop checks passed")
        elif args.command == "start-thread":
            print(start_thread(root, args).relative_to(root))
        elif args.command == "park-thread":
            print(park_thread(root, args).relative_to(root))
        elif args.command == "resume-thread":
            print(resume_thread(root, args))
        elif args.command == "close-thread":
            print(close_thread(root, args).relative_to(root))
        elif args.command == "capture-learning":
            path = capture_learning(root, args)
            refresh(root)
            print(path.relative_to(root))
        elif args.command == "revisit-learning":
            path = revisit_learning(root, args)
            refresh(root)
            print(path.relative_to(root))
        else:
            parser.error(f"unsupported command: {args.command}")
    except (AgentLoopError, LearningLoopError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
