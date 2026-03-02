import json
from pathlib import Path

from scripts import migrate_model_providers_contract as migrate_script


def _write_registry(path: Path) -> None:
    payload = {
        "version": 1,
        "providers": {
            "gmn": {
                "baseUrl": "https://gmn.example.com/v1",
                "apiKey": "sk-test",
                "default_model": "gpt-5.2",
            }
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_migrate_registry_writes_backup_to_explicit_backup_dir(tmp_path):
    registry_path = tmp_path / "model_providers.local.json"
    backup_dir = tmp_path / "safe-backups"
    _write_registry(registry_path)

    changed = migrate_script.migrate_registry(
        path=registry_path,
        dry_run=False,
        backup_dir=backup_dir,
    )

    assert changed >= 0
    backups = list(backup_dir.glob("model_providers.local.json.bak-*"))
    assert backups
    assert backups[0].is_file()
    assert not list(tmp_path.glob("model_providers.local.json.bak-*"))
