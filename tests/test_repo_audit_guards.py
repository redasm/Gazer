from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_license_matches_repository_license() -> None:
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    license_text = (PROJECT_ROOT / "LICENSE").read_text(encoding="utf-8")

    assert 'license = {text = "Apache-2.0"}' in pyproject
    assert "Apache License" in license_text


def test_dockerfile_does_not_copy_missing_workflows_directory() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY workflows/ ./workflows/" not in dockerfile
    assert not (PROJECT_ROOT / "workflows").exists()


def test_python_sources_are_utf8_without_bom() -> None:
    py_files = [
        *PROJECT_ROOT.glob("*.py"),
        *PROJECT_ROOT.glob("src/**/*.py"),
        *PROJECT_ROOT.glob("tests/**/*.py"),
    ]

    offenders = [
        str(path.relative_to(PROJECT_ROOT))
        for path in py_files
        if path.read_bytes().startswith(b"\xef\xbb\xbf")
    ]
    assert offenders == []
