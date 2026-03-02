"""One-shot migration from markdown memory archives to OpenViking backend."""

from __future__ import annotations

import argparse
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from memory.viking_backend import OpenVikingMemoryBackend

logger = logging.getLogger("GazerMemoryMigration")


_EVENT_BLOCK_RE = re.compile(
    r"### \[(.*?)\] (.*?)\n(.*?)(?=\n###|\Z)",
    re.DOTALL,
)
_KNOWLEDGE_DATE_RE = re.compile(r"\*\((\d{4}-\d{2}-\d{2})\)\*\s*$")


@dataclass(frozen=True)
class _MigrationRecord:
    content: str
    sender: str
    timestamp: datetime
    metadata: Dict[str, Any]
    source: str


def _normalize_text(text: str) -> str:
    return " ".join(str(text or "").strip().split())


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _collect_event_records(events_dir: Path) -> Tuple[List[_MigrationRecord], List[Dict[str, str]], int]:
    records: List[_MigrationRecord] = []
    failures: List[Dict[str, str]] = []
    files = sorted(events_dir.glob("*.md")) if events_dir.is_dir() else []
    for path in files:
        date_str = path.stem
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            failures.append(
                {
                    "source": str(path),
                    "reason": "invalid_event_filename_date",
                }
            )
            continue
        content = path.read_text(encoding="utf-8")
        blocks = _EVENT_BLOCK_RE.findall(content)
        for idx, (time_str, sender, body) in enumerate(blocks, start=1):
            body_text = body.strip()
            if not body_text:
                failures.append(
                    {
                        "source": f"{path}#block{idx}",
                        "reason": "empty_event_content",
                    }
                )
                continue
            try:
                ts = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
            except ValueError:
                failures.append(
                    {
                        "source": f"{path}#block{idx}",
                        "reason": f"invalid_event_timestamp:{time_str}",
                    }
                )
                continue
            records.append(
                _MigrationRecord(
                    content=body_text,
                    sender=sender.strip() or "unknown",
                    timestamp=ts,
                    metadata={
                        "source_type": "events_markdown",
                        "source_file": str(path),
                    },
                    source=f"{path}#block{idx}",
                )
            )
    return records, failures, len(files)


def _resolve_knowledge_timestamp(path: Path, line_payload: str) -> Tuple[datetime, str]:
    payload = str(line_payload or "").strip()
    match = _KNOWLEDGE_DATE_RE.search(payload)
    if match:
        date_str = match.group(1)
        payload = payload[: match.start()].rstrip()
        return datetime.fromisoformat(f"{date_str}T12:00:00"), payload
    ts = datetime.fromtimestamp(path.stat().st_mtime)
    return ts.replace(hour=12, minute=0, second=0, microsecond=0), payload


def _collect_knowledge_records(
    knowledge_dir: Path,
) -> Tuple[List[_MigrationRecord], List[Dict[str, str]], int]:
    records: List[_MigrationRecord] = []
    failures: List[Dict[str, str]] = []
    files = sorted(knowledge_dir.rglob("*.md")) if knowledge_dir.is_dir() else []
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        for line_no, raw in enumerate(lines, start=1):
            stripped = raw.strip()
            if not stripped.startswith("- "):
                continue
            ts, payload = _resolve_knowledge_timestamp(path, stripped[2:].strip())
            if not payload:
                failures.append(
                    {
                        "source": f"{path}#L{line_no}",
                        "reason": "empty_knowledge_item",
                    }
                )
                continue
            records.append(
                _MigrationRecord(
                    content=payload,
                    sender="system",
                    timestamp=ts,
                    metadata={
                        "source_type": "knowledge_markdown",
                        "source_file": str(path),
                        "knowledge_group": path.parent.name,
                        "knowledge_subject": path.stem,
                    },
                    source=f"{path}#L{line_no}",
                )
            )
    return records, failures, len(files)


def _dedupe_records(records: Iterable[_MigrationRecord]) -> Tuple[List[_MigrationRecord], int]:
    deduped: List[_MigrationRecord] = []
    seen: set[Tuple[str, str]] = set()
    duplicate_count = 0
    for rec in records:
        key = (rec.sender.strip().lower(), _normalize_text(rec.content).lower())
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        deduped.append(rec)
    return deduped, duplicate_count


def _write_report(path: Path, report: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


def migrate_markdown_memory_to_openviking(
    *,
    memory_dir: str = "assets/memory",
    openviking_data_dir: str = "data/openviking",
    dry_run: bool = True,
    report_path: str = "",
    session_prefix: str = "gazer",
    default_user: str = "owner",
    config_file: str = "",
    commit_every_messages: int = 8,
) -> Dict[str, Any]:
    """Migrate markdown-based memory archives into OpenViking backend storage."""

    memory_root = Path(memory_dir).resolve()
    openviking_root = Path(openviking_data_dir).resolve()
    events_dir = memory_root / "events"
    knowledge_dir = memory_root / "knowledge"

    event_records, event_failures, event_file_count = _collect_event_records(events_dir)
    knowledge_records, knowledge_failures, knowledge_file_count = _collect_knowledge_records(knowledge_dir)

    all_records = sorted([*event_records, *knowledge_records], key=lambda rec: rec.timestamp)
    deduped_records, deduped_in_batch = _dedupe_records(all_records)

    target_report_path = Path(report_path).resolve() if report_path else (
        openviking_root / ("migration_report_dry_run.json" if dry_run else "migration_report.json")
    )
    failed_records: List[Dict[str, str]] = [*event_failures, *knowledge_failures]
    report: Dict[str, Any] = {
        "dry_run": bool(dry_run),
        "memory_dir": str(memory_root),
        "openviking_data_dir": str(openviking_root),
        "event_files": event_file_count,
        "knowledge_files": knowledge_file_count,
        "event_records": len(event_records),
        "knowledge_records": len(knowledge_records),
        "planned_records": len(deduped_records),
        "imported_records": 0,
        "failed_records": failed_records,
        "duplicate_merges": {
            "deduplicated_in_batch": deduped_in_batch,
            "backend_merge_decisions": 0,
        },
        "report_path": str(target_report_path),
    }

    if dry_run:
        _write_report(target_report_path, report)
        return report

    backend = OpenVikingMemoryBackend(
        data_dir=openviking_root,
        session_prefix=session_prefix,
        default_user=default_user,
        config_file=config_file,
        commit_every_messages=commit_every_messages,
        enable_client=False,
    )
    decisions_before = _read_jsonl(openviking_root / "extraction_decisions.jsonl")
    imported = 0
    try:
        for rec in deduped_records:
            try:
                backend.add_memory(
                    content=rec.content,
                    sender=rec.sender,
                    timestamp=rec.timestamp,
                    metadata=rec.metadata,
                    from_reindex=False,
                )
                imported += 1
            except Exception as exc:
                failed_records.append(
                    {
                        "source": rec.source,
                        "reason": f"import_error:{type(exc).__name__}:{exc}",
                    }
                )
    finally:
        backend.close()

    decisions_after = _read_jsonl(openviking_root / "extraction_decisions.jsonl")
    new_decisions = decisions_after[len(decisions_before) :]
    backend_merge_decisions = sum(
        1
        for item in new_decisions
        if str(item.get("kind", "")) == "memory_extraction"
        and str(item.get("decision", "")) == "MERGE"
    )

    report["imported_records"] = imported
    report["failed_records"] = failed_records
    report["duplicate_merges"] = {
        "deduplicated_in_batch": deduped_in_batch,
        "backend_merge_decisions": backend_merge_decisions,
    }

    _write_report(target_report_path, report)
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Migrate markdown memory to OpenViking backend.")
    parser.add_argument("--memory-dir", default="assets/memory")
    parser.add_argument("--openviking-data-dir", default="data/openviking")
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--report-path", default="")
    parser.add_argument("--session-prefix", default="gazer")
    parser.add_argument("--default-user", default="owner")
    parser.add_argument("--config-file", default="")
    parser.add_argument("--commit-every-messages", type=int, default=8)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    report = migrate_markdown_memory_to_openviking(
        memory_dir=args.memory_dir,
        openviking_data_dir=args.openviking_data_dir,
        dry_run=bool(args.dry_run),
        report_path=args.report_path,
        session_prefix=args.session_prefix,
        default_user=args.default_user,
        config_file=args.config_file,
        commit_every_messages=args.commit_every_messages,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
