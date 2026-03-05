"""CLI subcommand: ``gazer plugin create|install|list``.

Provides developer tooling for the Gazer plugin ecosystem.
"""

import argparse
import logging
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import List

from plugins.loader import PluginLoader
from plugins.manifest import PluginManifest

logger = logging.getLogger("PluginCLI")


def register_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the ``plugin`` subcommand."""
    plugin_parser = subparsers.add_parser("plugin", help="Manage Gazer plugins")
    plugin_sub = plugin_parser.add_subparsers(dest="plugin_action")

    # --- plugin list ---
    plugin_sub.add_parser("list", help="List discovered plugins")

    # --- plugin create ---
    create_parser = plugin_sub.add_parser("create", help="Scaffold a new plugin")
    create_parser.add_argument("name", help="Plugin name (e.g. my-weather-tool)")
    create_parser.add_argument(
        "--dir", "-d", default="extensions",
        help="Parent directory to create the plugin in (default: extensions/)",
    )
    create_parser.add_argument(
        "--slot", "-s", default="tool",
        choices=["tool", "channel", "provider", "memory"],
        help="Plugin slot type (default: tool)",
    )

    # --- plugin install ---
    install_parser = plugin_sub.add_parser("install", help="Install a plugin from path or pip spec")
    install_parser.add_argument("source", help="Plugin source: local path or pip package spec")
    install_parser.add_argument(
        "--global", dest="global_install", action="store_true",
        help="Install to ~/.gazer/extensions/ instead of workspace extensions/",
    )

    plugin_parser.set_defaults(func=_handle_plugin)


def _handle_plugin(args: argparse.Namespace) -> None:
    action = getattr(args, "plugin_action", None)
    if not action:
        print("Usage: python -m cli plugin {list|create|install}")
        print("Run 'python -m cli plugin --help' for details.")
        return

    if action == "list":
        _plugin_list()
    elif action == "create":
        _plugin_create(args)
    elif action == "install":
        _plugin_install(args)


# ---------------------------------------------------------------------------
# plugin list
# ---------------------------------------------------------------------------

def _plugin_list() -> None:
    loader = PluginLoader(workspace=Path.cwd())
    manifests = loader.discover()
    if not manifests:
        print("No plugins discovered.")
        return
    print(f"Discovered {len(manifests)} plugin(s):\n")
    for m in manifests.values():
        status = "optional" if m.optional else "bundled"
        print(f"  {m.id:30s}  v{m.version}  [{m.slot}]  ({status})")
        if m.name != m.id:
            print(f"    {m.name}")
    print()


# ---------------------------------------------------------------------------
# plugin create — scaffolding
# ---------------------------------------------------------------------------

_MANIFEST_TEMPLATE = textwrap.dedent("""\
    id: {plugin_id}
    name: {plugin_name}
    version: 0.1.0
    slot: {slot}
    entry: plugin:setup
    optional: true
    skills: []
    config_schema:
      type: object
      properties: {{}}
    requires:
      python: ">=3.10"
      packages: []
""")

_PLUGIN_PY_TEMPLATE = textwrap.dedent("""\
    \"\"\"Gazer plugin: {plugin_name}.\"\"\"

    from plugins.api import PluginAPI
    from tools.base import Tool
    from typing import Any, Dict


class {class_name}(Tool):
        \"\"\"Example plugin tool scaffold for {plugin_name}.\"\"\"

        @property
        def name(self) -> str:
            return "{tool_name}"

        @property
        def description(self) -> str:
            return "Scaffolded plugin tool that echoes input text."

        @property
        def parameters(self) -> Dict[str, Any]:
            return {{
                "type": "object",
                "properties": {{
                    "query": {{
                        "type": "string",
                        "description": "Input query",
                    }},
                }},
                "required": ["query"],
            }}

        async def execute(self, **kwargs: Any) -> str:
            query = kwargs.get("query", "")
            # Replace this echo with real plugin business logic.
            return f"{{self.name}} received: {{query}}"


    def setup(api: PluginAPI) -> None:
        \"\"\"Plugin entry point — called by PluginLoader.\"\"\"
        cfg = api.config
        api.register_tool({class_name}())
""")

_README_TEMPLATE = textwrap.dedent("""\
    # {plugin_name}

    A Gazer plugin.

    ## Installation

    Copy this directory to `extensions/` in your Gazer workspace, or to
    `~/.gazer/extensions/` for global availability.

    ## Configuration

    Add the following to your `config/settings.yaml`:

    ```yaml
    plugins:
      enabled:
        - {plugin_id}
      {plugin_id}: {{}}
    ```

    ## Development

    Edit `plugin.py` to implement your tool logic. The `setup()` function
    is the entry point called by the Gazer plugin loader.
""")


def _plugin_create(args: argparse.Namespace) -> None:
    plugin_id = args.name.lower().replace(" ", "-")
    plugin_name = args.name.replace("-", " ").title()
    class_name = plugin_name.replace(" ", "") + "Tool"
    tool_name = plugin_id.replace("-", "_")
    slot = args.slot

    target_dir = Path(args.dir) / plugin_id
    if target_dir.exists():
        print(f"Error: directory '{target_dir}' already exists.", file=sys.stderr)
        sys.exit(1)

    target_dir.mkdir(parents=True, exist_ok=True)

    # gazer_plugin.yaml
    manifest_content = _MANIFEST_TEMPLATE.format(
        plugin_id=plugin_id,
        plugin_name=plugin_name,
        slot=slot,
    )
    (target_dir / "gazer_plugin.yaml").write_text(manifest_content, encoding="utf-8")

    # plugin.py
    plugin_content = _PLUGIN_PY_TEMPLATE.format(
        plugin_name=plugin_name,
        class_name=class_name,
        tool_name=tool_name,
    )
    (target_dir / "plugin.py").write_text(plugin_content, encoding="utf-8")

    # README.md
    readme_content = _README_TEMPLATE.format(
        plugin_id=plugin_id,
        plugin_name=plugin_name,
    )
    (target_dir / "README.md").write_text(readme_content, encoding="utf-8")

    # __init__.py
    (target_dir / "__init__.py").write_text("", encoding="utf-8")

    print(f"Created plugin scaffold at {target_dir}/")
    print(f"  gazer_plugin.yaml  — manifest")
    print(f"  plugin.py          — entry point (edit this!)")
    print(f"  README.md          — documentation template")
    print(f"\nNext steps:")
    print(f"  1. Edit {target_dir / 'plugin.py'} to implement your tool")
    print(f"  2. Add '{plugin_id}' to plugins.enabled in config/settings.yaml")


# ---------------------------------------------------------------------------
# plugin install
# ---------------------------------------------------------------------------

def _plugin_install(args: argparse.Namespace) -> None:
    source = args.source
    global_install = args.global_install

    target_base = (
        Path.home() / ".gazer" / "extensions"
        if global_install
        else Path("extensions")
    )
    target_base.mkdir(parents=True, exist_ok=True)

    source_path = Path(source)
    if source_path.is_dir():
        # Local directory install — copy
        manifest_path = source_path / "gazer_plugin.yaml"
        if not manifest_path.exists():
            print(f"Error: {source_path} does not contain gazer_plugin.yaml", file=sys.stderr)
            sys.exit(1)
        plugin_id = source_path.name
        dest = target_base / plugin_id
        if dest.exists():
            print(f"Removing existing installation at {dest}")
            shutil.rmtree(dest)
        shutil.copytree(source_path, dest)
        print(f"Installed '{plugin_id}' to {dest}")
    else:
        # Treat as pip package spec
        print(f"Installing pip package: {source}")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", source],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"pip install failed:\n{result.stderr}", file=sys.stderr)
            sys.exit(1)
        print(f"Installed '{source}' via pip.")
        print("Note: pip-installed plugins are auto-discovered if named gazer-plugin-* or gazer_plugin_*")

    print(f"\nDon't forget to add the plugin id to plugins.enabled in your config.")
