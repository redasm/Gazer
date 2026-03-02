"""Cron Scheduler plugin — bundled Layer 2.

Requires service: cron_scheduler (CronScheduler instance).
"""

from plugins.api import PluginAPI
from tools.cron_tool import CronTool


def setup(api: PluginAPI) -> None:
    scheduler = api.get_service("cron_scheduler")
    if not scheduler:
        return  # Cron not enabled
    api.register_tool(CronTool(scheduler))
