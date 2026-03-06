from pathlib import Path

import yaml

from config.defaults import DEFAULT_CONFIG
from tools.admin import ROUTERS


def test_default_config_removes_legacy_agent_orchestrator_keys():
    agents_cfg = DEFAULT_CONFIG["agents"]

    assert set(agents_cfg.keys()) == {"defaults"}


def test_admin_router_list_excludes_legacy_agents_router():
    tags = [tuple(router_tags) for _router, _prefix, router_tags in ROUTERS if _router is not None]

    assert ("agents",) not in tags


def test_runtime_settings_file_removes_legacy_multi_agent_keys():
    settings_path = Path("config/settings.yaml")
    payload = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    agents_cfg = payload.get("agents", {}) or {}

    assert "list" not in agents_cfg
    assert "bindings" not in agents_cfg
    assert "orchestrator" not in agents_cfg
    assert "templates" not in agents_cfg


def test_runtime_settings_file_removes_delegate_task_residue():
    settings_text = Path("config/settings.yaml").read_text(encoding="utf-8")
    assert "delegate_task" not in settings_text


def test_deprecated_src_config_settings_file_removed():
    assert not Path("src/config/settings.yaml").exists()


def test_web_app_removes_legacy_admin_token_bootstrap():
    app_text = Path("web/src/App.jsx").read_text(encoding="utf-8")
    assert "bootstrapLegacyToken" not in app_text
    assert "admin_token" not in app_text
