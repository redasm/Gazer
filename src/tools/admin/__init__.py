"""Gazer Admin API -- modular router package.

Shared state (globals injected by ``brain.py``) lives in ``_shared.py``.
"""

from tools.admin import (
    auth, git, cron, skills, websockets, gateway, agents,
    evolution, plugins, config_routes, deployment, policy,
    memory, workflows, mcp_routes, logs,
    observability, debug, satellite, system,
    whatsapp_webhook, channel_webhooks,
)


def _get_router(module):
    """Get the APIRouter from a module (``router`` or ``app``)."""
    return getattr(module, "router", None) or getattr(module, "app", None)


# All 18 router modules, ordered by domain
ROUTERS = [
    (_get_router(auth), "", ["auth"]),
    (_get_router(git), "", ["git"]),
    (_get_router(cron), "", ["cron"]),
    (_get_router(skills), "", ["skills"]),
    (_get_router(websockets), "", ["websockets"]),
    (_get_router(evolution), "", ["evolution"]),
    (_get_router(plugins), "", ["plugins"]),
    (_get_router(config_routes), "", ["config"]),
    (_get_router(deployment), "", ["deployment"]),
    (_get_router(policy), "", ["policy"]),
    (_get_router(memory), "", ["memory"]),
    (_get_router(workflows), "", ["workflows"]),
    (_get_router(mcp_routes), "", ["mcp"]),
    (_get_router(logs), "", ["logs"]),
    (_get_router(observability), "", ["observability"]),
    (_get_router(debug), "", ["debug"]),
    (_get_router(satellite), "", ["satellite"]),
    (_get_router(system), "", ["system"]),
    (_get_router(gateway), "", ["gateway"]),
    (_get_router(agents), "", ["agents"]),
    (_get_router(whatsapp_webhook), "", ["whatsapp"]),
    (_get_router(channel_webhooks), "", ["channel-webhooks"]),
]
