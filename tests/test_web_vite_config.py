from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VITE_CONFIG_PATH = PROJECT_ROOT / "web" / "vite.config.js"


def test_vite_config_dedupes_react_runtime_dependencies() -> None:
    source = VITE_CONFIG_PATH.read_text(encoding="utf-8")

    assert "dedupe: ['react', 'react-dom']" in source
    assert "optimizeDeps:" in source
    assert "force: true" in source
    assert "'react'" in source
    assert "'react-dom'" in source
    assert "'react-dom/client'" in source
    assert "'axios'" in source


def test_vite_dev_server_disables_browser_cache_for_hot_dependencies() -> None:
    source = VITE_CONFIG_PATH.read_text(encoding="utf-8")

    assert "server:" in source
    assert "headers:" in source
    assert "'Cache-Control': 'no-store'" in source
