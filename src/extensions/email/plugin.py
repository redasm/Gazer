"""Email Tools plugin — bundled Layer 2.

Requires service: email_client (EmailClient instance).
Only registers tools if email_client is available.
"""

from plugins.api import PluginAPI
from tools.email_tools import (
    EmailListTool,
    EmailReadTool,
    EmailSendTool,
    EmailSearchTool,
)


def setup(api: PluginAPI) -> None:
    email_client = api.get_service("email_client")
    if not email_client:
        return  # Email not configured, skip silently
    for tool in [
        EmailListTool(email_client),
        EmailReadTool(email_client),
        EmailSendTool(email_client),
        EmailSearchTool(email_client),
    ]:
        api.register_tool(tool)
