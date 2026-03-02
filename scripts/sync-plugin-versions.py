#!/usr/bin/env python3
"""Sync bundled plugin manifest versions with the main project version.

Usage:
    python scripts/sync-plugin-versions.py          # dry-run (report only)
    python scripts/sync-plugin-versions.py --apply   # actually update files
"""

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS_DIR = PROJECT_ROOT / "extensions"


def get_project_version() -> str:
    """Read version from pyproject.toml."""
    pyproject = PROJECT_ROOT / "pyproject.toml"
    content = pyproject.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if not match:
        print("ERROR: could not find version in pyproject.toml", file=sys.stderr)
        sys.exit(1)
    return match.group(1)


def find_plugin_manifests() -> list[Path]:
    """Find all gazer_plugin.yaml files in extensions/."""
    if not EXTENSIONS_DIR.is_dir():
        return []
    return sorted(EXTENSIONS_DIR.glob("*/gazer_plugin.yaml"))


def sync_manifest(path: Path, target_version: str, apply: bool) -> bool:
    """Update version in a plugin manifest. Returns True if changed."""
    content = path.read_text(encoding="utf-8")
    pattern = re.compile(r"^(version:\s*)(.+)$", re.MULTILINE)
    match = pattern.search(content)
    if not match:
        print(f"  WARN: no version field in {path}")
        return False

    current = match.group(2).strip().strip("'\"")
    if current == target_version:
        return False

    if apply:
        new_content = pattern.sub(f"\\g<1>{target_version}", content)
        path.write_text(new_content, encoding="utf-8")

    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync plugin manifest versions")
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    args = parser.parse_args()

    version = get_project_version()
    print(f"Project version: {version}")

    manifests = find_plugin_manifests()
    if not manifests:
        print("No plugin manifests found in extensions/")
        return

    changed = 0
    for path in manifests:
        plugin_id = path.parent.name
        content = path.read_text(encoding="utf-8")
        match = re.search(r"^version:\s*(.+)$", content, re.MULTILINE)
        current = match.group(1).strip().strip("'\"") if match else "?"

        if sync_manifest(path, version, args.apply):
            action = "UPDATED" if args.apply else "NEEDS UPDATE"
            print(f"  {plugin_id:30s}  {current} → {version}  [{action}]")
            changed += 1
        else:
            print(f"  {plugin_id:30s}  {current}  [OK]")

    if changed:
        if args.apply:
            print(f"\nUpdated {changed} manifest(s).")
        else:
            print(f"\n{changed} manifest(s) need updating. Run with --apply to fix.")
            sys.exit(1)
    else:
        print("\nAll manifests in sync.")


if __name__ == "__main__":
    main()
