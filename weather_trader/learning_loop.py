from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable


LEARNING_STATUSES = {"CAPTURED", "REVISIT", "INTEGRATED", "SUPERSEDED"}
OPEN_LEARNING_STATUSES = {"CAPTURED", "REVISIT"}
LEARNING_KINDS = {"CONCEPT", "FAILURE", "DESIGN", "EXPERIENCE"}
MAX_OPEN_LEARNINGS = 20
LEARNING_RE = re.compile(r"^L(\d{4})-[a-z0-9][a-z0-9-]*\.md$")


class LearningLoopError(RuntimeError):
    pass


@dataclass(frozen=True)
class LearningRecord:
    path: Path
    metadata: dict[str, str]
    body: str

    @property
    def learning_id(self) -> str:
        return self.metadata["id"]

    @property
    def status(self) -> str:
        return self.metadata["status"]


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:64].rstrip("-") or "learning"


def _validate_date(value: str, field: str = "revisit_on") -> None:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise LearningLoopError(f"{field} must be YYYY-MM-DD: {value}") from exc


def parse_learning(path: Path) -> LearningRecord:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise LearningLoopError(f"{path}: missing metadata block")
    try:
        raw_metadata, body = text[4:].split("\n---\n", 1)
    except ValueError as exc:
        raise LearningLoopError(f"{path}: unterminated metadata block") from exc
    metadata: dict[str, str] = {}
    for line in raw_metadata.splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise LearningLoopError(f"{path}: invalid metadata line: {line}")
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip()
    required = {"id", "title", "status", "kind", "captured", "last_revisited", "revisit_on", "origin"}
    missing = sorted(required - metadata.keys())
    if missing:
        raise LearningLoopError(f"{path}: missing metadata: {', '.join(missing)}")
    if metadata["status"] not in LEARNING_STATUSES:
        raise LearningLoopError(f"{path}: invalid status {metadata['status']}")
    if metadata["kind"] not in LEARNING_KINDS:
        raise LearningLoopError(f"{path}: invalid kind {metadata['kind']}")
    _validate_date(metadata["revisit_on"])
    if metadata["origin"] != "none" and not re.fullmatch(r"T\d{4}", metadata["origin"]):
        raise LearningLoopError(f"{path}: origin must be T#### or none")
    return LearningRecord(path=path, metadata=metadata, body=body)


def render_learning(metadata: dict[str, str], body: str) -> str:
    keys = ("id", "title", "status", "kind", "captured", "last_revisited", "revisit_on", "origin")
    lines = ["---", *(f"{key}: {metadata[key]}" for key in keys), "---", "", body.strip(), ""]
    return "\n".join(lines)


def section_map(body: str) -> dict[str, str]:
    matches = list(re.finditer(r"^## ([^\n]+)\s*$", body, re.MULTILINE))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[match.group(1).strip()] = body[match.end() : end].strip()
    return sections


def discover_learnings(root: Path) -> list[LearningRecord]:
    learning_root = root / "learning"
    paths = [path for path in learning_root.glob("L????-*.md") if path.name != "LEARNING_TEMPLATE.md"]
    records = [parse_learning(path) for path in sorted(paths)]
    seen: set[str] = set()
    for record in records:
        if record.learning_id in seen:
            raise LearningLoopError(f"duplicate learning id: {record.learning_id}")
        seen.add(record.learning_id)
    return records


def learning_counts(root: Path) -> dict[str, int]:
    records = discover_learnings(root)
    counts = {status.lower(): sum(record.status == status for record in records) for status in LEARNING_STATUSES}
    counts["open"] = sum(record.status in OPEN_LEARNING_STATUSES for record in records)
    counts["overdue"] = sum(
        record.status in OPEN_LEARNING_STATUSES and record.metadata["revisit_on"] < _today() for record in records
    )
    return counts


def _next_id(records: Iterable[LearningRecord]) -> str:
    numbers = [int(record.learning_id[1:]) for record in records]
    return f"L{(max(numbers, default=0) + 1):04d}"


def _learning_by_id(root: Path, learning_id: str) -> LearningRecord:
    normalized = learning_id.upper()
    matches = [record for record in discover_learnings(root) if record.learning_id == normalized]
    if not matches:
        raise LearningLoopError(f"unknown learning: {normalized}")
    return matches[0]


def render_learning_index(records: Iterable[LearningRecord], generated_at: str) -> str:
    records = list(records)
    open_records = sorted(
        (record for record in records if record.status in OPEN_LEARNING_STATUSES),
        key=lambda record: (record.metadata["revisit_on"], record.learning_id),
    )
    mature_records = sorted(
        (record for record in records if record.status not in OPEN_LEARNING_STATUSES),
        key=lambda record: record.learning_id,
        reverse=True,
    )
    lines = [
        "# Learning Index",
        "",
        "Generated by `scripts/agent_loop.py refresh`. Do not edit this file by hand.",
        "",
        f"Generated at: {generated_at}",
        f"Open learning cards: {len(open_records)}/{MAX_OPEN_LEARNINGS}",
        "",
        "## Revisit Queue",
        "",
        "| ID | Status | Kind | Concept | Origin | Revisit on |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if open_records:
        for record in open_records:
            due = "OVERDUE " if record.metadata["revisit_on"] < _today() else ""
            lines.append(
                f"| [{record.learning_id}]({record.path.name}) | {record.status} | {record.metadata['kind']} | "
                f"{record.metadata['title']} | {record.metadata['origin']} | {due}{record.metadata['revisit_on']} |"
            )
    else:
        lines.append("| — | — | — | Nothing waiting for review. | — | — |")
    lines.extend(("", "## Integrated Or Superseded", "", "| ID | Status | Concept | Last revisited |", "| --- | --- | --- | --- |"))
    if mature_records:
        for record in mature_records:
            lines.append(
                f"| [{record.learning_id}]({record.path.name}) | {record.status} | {record.metadata['title']} | "
                f"{record.metadata['last_revisited']} |"
            )
    else:
        lines.append("| — | — | No mature cards yet. | — |")
    lines.extend(
        (
            "",
            "## Maturity",
            "",
            "- `CAPTURED`: a fresh observation worth preserving; interpretation may still change.",
            "- `REVISIT`: selected for deliberate reflection, connection, or application.",
            "- `INTEGRATED`: revisited and connected to a durable mental model or practice.",
            "- `SUPERSEDED`: retained historically but replaced by a better explanation.",
            "",
        )
    )
    return "\n".join(lines)


def write_learning_index(root: Path, generated_at: str | None = None) -> str:
    content = render_learning_index(discover_learnings(root), generated_at or _timestamp())
    path = root / "learning" / "INDEX.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return content


def capture_learning(root: Path, args: argparse.Namespace) -> Path:
    _validate_date(args.revisit_on)
    if args.origin != "none" and not re.fullmatch(r"T\d{4}", args.origin):
        raise LearningLoopError("origin must be T#### or none")
    records = discover_learnings(root)
    open_records = [record for record in records if record.status in OPEN_LEARNING_STATUSES]
    if len(open_records) >= MAX_OPEN_LEARNINGS:
        raise LearningLoopError(f"open-learning cap reached ({MAX_OPEN_LEARNINGS}); revisit or integrate a card first")
    learning_id = _next_id(records)
    metadata = {
        "id": learning_id,
        "title": args.title.strip(),
        "status": args.status,
        "kind": args.kind,
        "captured": _today(),
        "last_revisited": "never",
        "revisit_on": args.revisit_on,
        "origin": args.origin,
    }
    body = f"""# {learning_id} {args.title.strip()}

## Why This Mattered

{args.why.strip()}

## What Happened

{args.happened.strip()}

## Concept

{args.concept.strip()}

## Intuition

{args.intuition.strip()}

## General Pattern

{args.pattern.strip()}

## RoboWeather Application

{args.application.strip()}

## Questions To Revisit

{args.questions.strip()}

## Evidence And Sources

{chr(10).join(f'- {item.strip()}' for item in (args.evidence or ['No external source recorded.']))}

## Revisit Log

No revisits yet.
"""
    path = root / "learning" / f"{learning_id}-{_slugify(args.title)}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_learning(metadata, body), encoding="utf-8")
    write_learning_index(root)
    return path


def revisit_learning(root: Path, args: argparse.Namespace) -> Path:
    _validate_date(args.revisit_on)
    record = _learning_by_id(root, args.learning_id)
    if record.status == "SUPERSEDED" and args.status != "SUPERSEDED":
        raise LearningLoopError(f"{record.learning_id} is superseded; capture a new card instead of rewriting history")
    metadata = dict(record.metadata)
    metadata.update({"status": args.status, "last_revisited": _today(), "revisit_on": args.revisit_on})
    sections = section_map(record.body)
    prior_log = sections.get("Revisit Log", "No revisits yet.")
    if prior_log == "No revisits yet.":
        prior_log = ""
    entry = f"""### {_today()}

{args.reflection.strip()}

- New connection: {args.connection.strip()}
- Practice or action: {args.action.strip()}
- Maturity after review: {args.status}
"""
    body_parts: list[str] = []
    for heading in (
        "Why This Mattered",
        "What Happened",
        "Concept",
        "Intuition",
        "General Pattern",
        "RoboWeather Application",
        "Questions To Revisit",
        "Evidence And Sources",
    ):
        body_parts.append(f"## {heading}\n\n{sections[heading]}")
    body_parts.append(f"## Revisit Log\n\n{prior_log}\n\n{entry}".strip())
    body = f"# {record.learning_id} {record.metadata['title']}\n\n" + "\n\n".join(body_parts)
    record.path.write_text(render_learning(metadata, body), encoding="utf-8")
    write_learning_index(root)
    return record.path


def check_learning(root: Path) -> list[str]:
    errors: list[str] = []
    try:
        records = discover_learnings(root)
    except LearningLoopError as exc:
        return [str(exc)]
    open_records = [record for record in records if record.status in OPEN_LEARNING_STATUSES]
    if len(open_records) > MAX_OPEN_LEARNINGS:
        errors.append(f"open-learning cap exceeded: {len(open_records)}/{MAX_OPEN_LEARNINGS}")
    required_sections = (
        "Why This Mattered",
        "What Happened",
        "Concept",
        "Intuition",
        "General Pattern",
        "RoboWeather Application",
        "Questions To Revisit",
        "Evidence And Sources",
        "Revisit Log",
    )
    for record in records:
        if not LEARNING_RE.match(record.path.name) or not record.path.name.startswith(record.learning_id):
            errors.append(f"{record.path}: invalid learning filename")
        sections = section_map(record.body)
        for section in required_sections:
            if not sections.get(section, "").strip():
                errors.append(f"{record.path}: missing or empty section {section}")
    if not (root / "learning" / "INDEX.md").exists():
        errors.append("learning/INDEX.md is missing; run refresh")
    return errors
