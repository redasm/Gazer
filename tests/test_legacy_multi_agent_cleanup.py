from config.defaults import DEFAULT_CONFIG
from tools.admin import ROUTERS


def test_default_config_removes_legacy_agent_orchestrator_keys():
    agents_cfg = DEFAULT_CONFIG["agents"]

    assert set(agents_cfg.keys()) == {"defaults"}


def test_admin_router_list_excludes_legacy_agents_router():
    tags = [tuple(router_tags) for _router, _prefix, router_tags in ROUTERS if _router is not None]

    assert ("agents",) not in tags
