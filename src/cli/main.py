"""Gazer CLI — click-based command entry point.

Installed as ``gazer`` console script via pyproject.toml.

Subcommands:
    gazer start            Launch full Brain + Admin API
    gazer chat             Interactive REPL (coding agent)
    gazer onboard          Guided setup wizard
    gazer doctor           System diagnostics (no Brain needed)
    gazer pairing          Manage DM pairing allowlist
    gazer config show      Print current config (redacted)
    gazer channel status   Check channel token validity
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

import click

# Resolve project root early so imports work before pip install -e
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC = _PROJECT_ROOT / "src"
if _SRC.exists() and str(_SRC) not in sys.path:
    sys.path.append(str(_SRC))

logger = logging.getLogger("GazerCLI")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )


# ── Root group ────────────────────────────────────────────────────────

@click.group(invoke_without_command=True)
@click.option("-v", "--verbose", is_flag=True, help="Enable DEBUG logging.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Gazer — desktop embodied AI companion."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    _setup_logging(verbose)
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ── gazer start ───────────────────────────────────────────────────────

@cli.command()
@click.pass_context
def start(ctx: click.Context) -> None:
    """Launch full Gazer Brain + Admin API."""
    from dotenv import load_dotenv
    load_dotenv()

    if sys.platform == "win32":
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            logger.debug("Failed to set Windows selector event loop policy", exc_info=True)

    # Re-use the existing main() from main.py
    sys.path.insert(0, str(_PROJECT_ROOT))
    from main import main as _main
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        click.echo("Gazer shutting down...")


# ── gazer chat ────────────────────────────────────────────────────────

@cli.command()
@click.option("-w", "--workspace", type=click.Path(exists=True), default=".",
              help="Workspace directory.")
@click.pass_context
def chat(ctx: click.Context, workspace: str) -> None:
    """Start interactive REPL (coding agent)."""
    from cli.interactive import InteractiveCLI
    cli_instance = InteractiveCLI(Path(workspace).resolve())
    try:
        asyncio.run(cli_instance.run())
    except KeyboardInterrupt:
        click.echo("\nGazer CLI interrupted.")


# ── gazer onboard ─────────────────────────────────────────────────────

@cli.command()
@click.pass_context
def onboard(ctx: click.Context) -> None:
    """Interactive setup wizard for first-time configuration."""
    from cli.onboard import run_onboard
    run_onboard()


# ── gazer doctor ──────────────────────────────────────────────────────

@cli.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Run system diagnostics (no Brain needed)."""
    _run_doctor()


def _run_doctor() -> None:
    """Standalone doctor — does not require a running Brain."""
    from dotenv import load_dotenv
    load_dotenv()

    ok = click.style("✓", fg="green")
    warn = click.style("✗", fg="yellow")
    issues = 0

    click.echo(click.style("Running diagnostics...\n", bold=True))

    # 1. Python version
    v = sys.version_info
    if v >= (3, 10):
        click.echo(f"  {ok} Python {v.major}.{v.minor}.{v.micro}")
    else:
        click.echo(f"  {warn} Python {v.major}.{v.minor} (need >= 3.10)")
        issues += 1

    # 2. Core dependencies
    missing_deps = []
    for mod in ["fastapi", "pydantic", "uvicorn", "openai", "litellm", "yaml", "dotenv"]:
        try:
            __import__(mod)
        except ImportError:
            missing_deps.append(mod)
    if missing_deps:
        click.echo(f"  {warn} Missing packages: {', '.join(missing_deps)}")
        issues += 1
    else:
        click.echo(f"  {ok} Core dependencies installed")

    # 3. Config file
    from runtime.config_manager import config
    settings_path = _PROJECT_ROOT / "config" / "settings.yaml"
    if settings_path.exists():
        click.echo(f"  {ok} config/settings.yaml found")
    else:
        click.echo(f"  {warn} config/settings.yaml not found")
        issues += 1

    # 4. .env file
    env_path = _PROJECT_ROOT / ".env"
    if env_path.exists():
        click.echo(f"  {ok} .env file found")
    else:
        click.echo(f"  {warn} .env file not found (copy .env.example)")
        issues += 1

    # 5. LLM API keys
    has_key = False
    for key_env in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY",
                     "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"]:
        val = os.environ.get(key_env, "")
        if val and not val.startswith("your_"):
            has_key = True
            break
    if has_key:
        click.echo(f"  {ok} LLM API key configured")
    else:
        click.echo(f"  {warn} No LLM API key found in environment")
        issues += 1

    # 6. DM policy check
    dm_policy = config.get("security.dm_policy", "pairing")
    if dm_policy == "open":
        click.echo(f"  {warn} DM policy is 'open' — any user can chat with the bot")
        issues += 1
    else:
        click.echo(f"  {ok} DM policy: {dm_policy}")

    # 7. Owner configured
    owner_ids = config.get("security.owner_channel_ids", {})
    if owner_ids:
        click.echo(f"  {ok} Owner channel IDs configured")
    else:
        click.echo(f"  {warn} No owner_channel_ids set (pairing approvals won't work)")
        issues += 1

    # 8. Channel tokens
    channels_checked = 0
    for ch_name, env_key in [("Telegram", "TELEGRAM_BOT_TOKEN"),
                              ("Discord", "DISCORD_BOT_TOKEN"),
                              ("Feishu", "FEISHU_APP_ID")]:
        token = os.environ.get(env_key, "")
        if token and not token.startswith("your_"):
            click.echo(f"  {ok} {ch_name} token set")
            channels_checked += 1
    if channels_checked == 0:
        click.echo(f"  {warn} No channel tokens configured")

    # 9. Data directory
    data_dir = _PROJECT_ROOT / "data"
    if data_dir.exists():
        click.echo(f"  {ok} data/ directory exists")
    else:
        click.echo(f"  {warn} data/ directory missing (will be created on first run)")

    # 10. Disk space
    try:
        import shutil
        total, used, free = shutil.disk_usage(_PROJECT_ROOT)
        pct = used / total * 100
        sym = ok if pct < 90 else warn
        click.echo(f"  {sym} Disk: {pct:.0f}% used ({free // (1024**3)} GB free)")
    except Exception:
        logger.debug("Failed to inspect disk usage during doctor run", exc_info=True)

    # 11. Port availability
    import socket
    api_port = int(os.environ.get("ADMIN_API_PORT", config.get("web.port", 8080)))
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex(("127.0.0.1", api_port))
            if result == 0:
                click.echo(f"  {warn} Port {api_port} already in use")
                issues += 1
            else:
                click.echo(f"  {ok} Port {api_port} available")
    except Exception:
        logger.debug("Failed to inspect admin API port availability", exc_info=True)

    # Summary
    click.echo()
    if issues == 0:
        click.echo(click.style("  All checks passed ✓", fg="green", bold=True))
    else:
        click.echo(click.style(f"  {issues} issue(s) found", fg="yellow", bold=True))


# ── gazer pairing ─────────────────────────────────────────────────────

@cli.group()
def pairing() -> None:
    """Manage DM pairing allowlist."""
    pass


@pairing.command("list")
def pairing_list() -> None:
    """Show pending pairing requests."""
    from security.pairing import get_pairing_manager
    mgr = get_pairing_manager()
    pending = mgr.list_pending()
    if not pending:
        click.echo("No pending pairing requests.")
        return
    click.echo(click.style(f"Pending requests ({len(pending)}):", bold=True))
    for p in pending:
        click.echo(f"  [{p['code']}]  {p['channel']}:{p['sender_id']}  "
                    f"(expires in {p['expires_in']}s)")


@pairing.command("approve")
@click.argument("code")
def pairing_approve(code: str) -> None:
    """Approve a pairing code."""
    from security.pairing import get_pairing_manager
    mgr = get_pairing_manager()
    req = mgr.approve(code)
    if req:
        click.echo(click.style(f"Approved: {req.channel}:{req.sender_id}", fg="green"))
    else:
        click.echo(click.style(f"Code '{code}' not found or expired.", fg="yellow"))


@pairing.command("reject")
@click.argument("code")
def pairing_reject(code: str) -> None:
    """Reject a pairing code."""
    from security.pairing import get_pairing_manager
    mgr = get_pairing_manager()
    req = mgr.reject(code)
    if req:
        click.echo(f"Rejected: {req.channel}:{req.sender_id}")
    else:
        click.echo(click.style(f"Code '{code}' not found or expired.", fg="yellow"))


@pairing.command("revoke")
@click.argument("channel")
@click.argument("sender_id")
def pairing_revoke(channel: str, sender_id: str) -> None:
    """Revoke an approved sender."""
    from security.pairing import get_pairing_manager
    mgr = get_pairing_manager()
    if mgr.revoke(channel, sender_id):
        click.echo(click.style(f"Revoked: {channel}:{sender_id}", fg="green"))
    else:
        click.echo(click.style(f"Not found in approved list.", fg="yellow"))


@pairing.command("approved")
def pairing_approved() -> None:
    """Show all approved senders."""
    from security.pairing import get_pairing_manager
    mgr = get_pairing_manager()
    approved = mgr.list_approved()
    if not approved:
        click.echo("No approved senders.")
        return
    for channel, senders in approved.items():
        click.echo(click.style(f"{channel}:", bold=True))
        for s in senders:
            click.echo(f"  {s}")


# ── gazer config ──────────────────────────────────────────────────────

@cli.group()
def config() -> None:
    """View and manage configuration."""
    pass


@config.command("show")
@click.option("--raw", is_flag=True, help="Show raw values without redaction.")
def config_show(raw: bool) -> None:
    """Print current configuration (secrets redacted)."""
    from runtime.config_manager import config as cfg, is_sensitive_config_path
    import yaml as _yaml

    data = cfg.to_dict() if hasattr(cfg, "to_dict") else dict(cfg._data) if hasattr(cfg, "_data") else {}

    if not raw:
        _redact(data, "")

    click.echo(_yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=True))


def _redact(d: dict, prefix: str) -> None:
    """Recursively redact sensitive values."""
    from runtime.config_manager import is_sensitive_config_path
    for key in list(d.keys()):
        path = f"{prefix}.{key}" if prefix else key
        val = d[key]
        if isinstance(val, dict):
            _redact(val, path)
        elif is_sensitive_config_path(path):
            d[key] = "***"
        elif isinstance(val, str) and any(s in key.lower() for s in ("key", "token", "secret", "password")):
            d[key] = "***"

# ── gazer plugin ───────────────────────────────────────────────────────────

@cli.group()
def plugin() -> None:
    """Manage Gazer plugins."""
    pass


@plugin.command("list")
def plugin_list() -> None:
    """List discovered plugins and their status."""
    from plugins.loader import PluginLoader
    loader = PluginLoader(workspace=Path.cwd())
    manifests = loader.discover()
    if not manifests:
        click.echo("No plugins discovered.")
        return
    click.echo(click.style(f"Discovered {len(manifests)} plugin(s):\n", bold=True))
    for m in manifests.values():
        status = "optional" if m.optional else "bundled"
        click.echo(f"  {m.id:30s}  v{m.version}  [{m.slot}]  ({status})")


@plugin.command("init")
@click.argument("name")
@click.option("--dir", "-d", default="extensions", help="Parent directory.")
@click.option("--slot", "-s", default="tool",
              type=click.Choice(["tool", "channel", "provider", "memory"]))
def plugin_init(name: str, dir: str, slot: str) -> None:
    """Scaffold a new plugin."""
    import argparse
    from cli.plugin_cmd import _plugin_create
    args = argparse.Namespace(name=name, dir=dir, slot=slot)
    _plugin_create(args)


@plugin.command("install")
@click.argument("source")
@click.option("--global", "global_install", is_flag=True, help="Install to ~/.gazer/extensions/")
def plugin_install(source: str, global_install: bool) -> None:
    """Install a plugin from path or pip spec."""
    import argparse
    from cli.plugin_cmd import _plugin_install
    args = argparse.Namespace(source=source, global_install=global_install)
    _plugin_install(args)


@plugin.command("enable")
@click.argument("plugin_id")
def plugin_enable(plugin_id: str) -> None:
    """Enable a plugin (add to plugins.enabled)."""
    from runtime.config_manager import config
    enabled = list(config.get("plugins.enabled", []) or [])
    disabled = list(config.get("plugins.disabled", []) or [])
    if plugin_id in enabled:
        click.echo(f"Plugin '{plugin_id}' is already enabled.")
        return
    enabled.append(plugin_id)
    if plugin_id in disabled:
        disabled.remove(plugin_id)
    config.set("plugins.enabled", enabled)
    config.set("plugins.disabled", disabled)
    click.echo(click.style(f"Enabled: {plugin_id}", fg="green"))
    click.echo("Restart Gazer for changes to take effect.")


@plugin.command("disable")
@click.argument("plugin_id")
def plugin_disable(plugin_id: str) -> None:
    """Disable a plugin (add to plugins.disabled)."""
    from runtime.config_manager import config
    enabled = list(config.get("plugins.enabled", []) or [])
    disabled = list(config.get("plugins.disabled", []) or [])
    if plugin_id in disabled:
        click.echo(f"Plugin '{plugin_id}' is already disabled.")
        return
    disabled.append(plugin_id)
    if plugin_id in enabled:
        enabled.remove(plugin_id)
    config.set("plugins.enabled", enabled)
    config.set("plugins.disabled", disabled)
    click.echo(f"Disabled: {plugin_id}")
    click.echo("Restart Gazer for changes to take effect.")


@plugin.command("info")
@click.argument("plugin_id")
def plugin_info(plugin_id: str) -> None:
    """Show plugin manifest and config schema."""
    from plugins.loader import PluginLoader
    loader = PluginLoader(workspace=Path.cwd())
    manifests = loader.discover()
    m = manifests.get(plugin_id)
    if not m:
        click.echo(click.style(f"Plugin '{plugin_id}' not found.", fg="yellow"))
        return
    click.echo(click.style(f"{m.name}", bold=True))
    click.echo(f"  ID:      {m.id}")
    click.echo(f"  Version: {m.version}")
    click.echo(f"  Slot:    {m.slot}")
    click.echo(f"  Entry:   {m.entry}")
    click.echo(f"  Optional:{m.optional}")
    if hasattr(m, 'config_schema') and m.config_schema:
        import yaml as _yaml
        click.echo(f"  Config schema:")
        click.echo(_yaml.dump(m.config_schema, default_flow_style=False).rstrip())


# ── gazer channel ─────────────────────────────────────────────────────────

@cli.group()
def channel() -> None:
    """Channel management."""
    pass


@channel.command("status")
def channel_status() -> None:
    """Check configured channel tokens."""
    from dotenv import load_dotenv
    load_dotenv()

    channels = [
        ("Telegram", "TELEGRAM_BOT_TOKEN"),
        ("Discord", "DISCORD_BOT_TOKEN"),
        ("Slack", "SLACK_BOT_TOKEN"),
        ("Feishu", "FEISHU_APP_ID"),
    ]

    ok = click.style("✓", fg="green")
    warn = click.style("✗", fg="yellow")
    dim = click.style("-", fg="bright_black")

    for name, env_key in channels:
        token = os.environ.get(env_key, "")
        if not token or token.startswith("your_"):
            click.echo(f"  {dim} {name}: not configured")
        else:
            click.echo(f"  {ok} {name}: token set ({token[:8]}...)")


# ── Entry point ───────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
