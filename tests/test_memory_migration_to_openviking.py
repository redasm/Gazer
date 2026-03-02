"""Tests for markdown-to-OpenViking migration utility."""

from __future__ import annotations

import json
from pathlib import Path

from memory.migration_to_openviking import migrate_markdown_memory_to_openviking


def _prepare_legacy_memory(base_dir: Path) -> None:
    events_dir = base_dir / "events"
    knowledge_topics_dir = base_dir / "knowledge" / "topics"
    knowledge_entities_dir = base_dir / "knowledge" / "entities"
    events_dir.mkdir(parents=True, exist_ok=True)
    knowledge_topics_dir.mkdir(parents=True, exist_ok=True)
    knowledge_entities_dir.mkdir(parents=True, exist_ok=True)

    (events_dir / "2026-02-01.md").write_text(
        "### [09:00:00] user\n我喜欢咖啡。\n\n"
        "### [09:05:00] assistant\n收到，我记住了。\n",
        encoding="utf-8",
    )
    (events_dir / "2026-02-02.md").write_text(
        "### [bad-time] user\n这一条时间戳是无效的。\n",
        encoding="utf-8",
    )

    (knowledge_topics_dir / "python.md").write_text(
        "# Knowledge: python\n\n"
        "- User prefers async pytest *(2026-02-03)*\n"
        "- User prefers async pytest *(2026-02-04)*\n",
        encoding="utf-8",
    )
    (knowledge_entities_dir / "user.md").write_text(
        "# Knowledge: user\n\n"
        "- User is a backend engineer\n",
        encoding="utf-8",
    )


def _jsonl_line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])


def test_memory_migration_supports_dry_run(tmp_path: Path):
    legacy_dir = tmp_path / "legacy_memory"
    openviking_dir = tmp_path / "openviking_store"
    _prepare_legacy_memory(legacy_dir)

    report = migrate_markdown_memory_to_openviking(
        memory_dir=str(legacy_dir),
        openviking_data_dir=str(openviking_dir),
        dry_run=True,
    )

    assert report["dry_run"] is True
    assert report["event_files"] == 2
    assert report["knowledge_files"] == 2
    assert report["event_records"] == 2
    assert report["knowledge_records"] == 3
    assert report["planned_records"] == 4
    assert report["imported_records"] == 0
    assert report["duplicate_merges"]["deduplicated_in_batch"] == 1
    assert any("invalid_event_timestamp" in item["reason"] for item in report["failed_records"])

    report_path = Path(report["report_path"])
    assert report_path.is_file()
    assert not (openviking_dir / "memory_events.jsonl").exists()


def test_memory_migration_imports_and_writes_report(tmp_path: Path):
    legacy_dir = tmp_path / "legacy_memory"
    openviking_dir = tmp_path / "openviking_store"
    _prepare_legacy_memory(legacy_dir)

    report = migrate_markdown_memory_to_openviking(
        memory_dir=str(legacy_dir),
        openviking_data_dir=str(openviking_dir),
        dry_run=False,
    )

    assert report["dry_run"] is False
    assert report["planned_records"] == 4
    assert report["imported_records"] == 4
    assert report["duplicate_merges"]["deduplicated_in_batch"] == 1
    assert report["duplicate_merges"]["backend_merge_decisions"] >= 0

    store_file = openviking_dir / "memory_events.jsonl"
    assert store_file.is_file()
    assert _jsonl_line_count(store_file) == 4

    sample = json.loads(store_file.read_text(encoding="utf-8").splitlines()[0])
    assert sample["metadata"]["source_type"] in {"events_markdown", "knowledge_markdown"}

    report_path = Path(report["report_path"])
    assert report_path.is_file()
