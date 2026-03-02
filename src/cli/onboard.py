"""Gazer onboard wizard — interactive first-time setup.

Guides the user through:
1. Environment check (Python version, core deps)
2. LLM provider selection + API key
3. Channel selection + tokens
4. Generate config/settings.yaml and .env
"""

import os
import sys
from pathlib import Path

import click
import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Supported LLM providers with their env key and example base URL
_LLM_PROVIDERS = [
    ("openai", "OPENAI_API_KEY", ""),
    ("deepseek", "DEEPSEEK_API_KEY", "https://api.deepseek.com/v1"),
    ("dashscope", "DASHSCOPE_API_KEY", ""),
    ("gemini", "GEMINI_API_KEY", ""),
    ("anthropic", "ANTHROPIC_API_KEY", ""),
    ("openrouter", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
    ("groq", "GROQ_API_KEY", "https://api.groq.com/openai/v1"),
    ("ollama (local)", "", "http://localhost:11434/v1"),
]

_CHANNELS = [
    ("telegram", "TELEGRAM_BOT_TOKEN", "Paste your Telegram Bot token (from @BotFather)"),
    ("discord", "DISCORD_BOT_TOKEN", "Paste your Discord Bot token"),
    ("slack", "SLACK_BOT_TOKEN", "Paste your Slack Bot token"),
    ("feishu", "FEISHU_APP_ID", "Paste your Feishu App ID"),
]


def run_onboard() -> None:
    """Run the interactive onboard wizard."""
    click.echo(click.style("\n  Gazer Setup Wizard\n", bold=True))

    # ── Step 1: environment check ──
    click.echo(click.style("Step 1: Environment check", bold=True))
    v = sys.version_info
    if v < (3, 10):
        click.echo(click.style(f"  Python {v.major}.{v.minor} detected — need >= 3.10", fg="red"))
        if not click.confirm("  Continue anyway?", default=False):
            return
    else:
        click.echo(f"  Python {v.major}.{v.minor}.{v.micro} ✓")

    click.echo()

    # ── Step 2: LLM provider ──
    click.echo(click.style("Step 2: LLM Provider", bold=True))
    click.echo("  Choose your primary LLM provider:\n")
    for i, (name, _, _) in enumerate(_LLM_PROVIDERS, 1):
        click.echo(f"    {i}. {name}")
    click.echo()

    choice = click.prompt("  Select provider", type=click.IntRange(1, len(_LLM_PROVIDERS)), default=1)
    provider_name, env_key, base_url = _LLM_PROVIDERS[choice - 1]

    env_vars: dict[str, str] = {}

    if env_key:
        api_key = click.prompt(f"  {env_key}", hide_input=True)
        env_vars[env_key] = api_key
    else:
        # Ollama local — no key needed
        host = click.prompt("  Ollama host", default="http://localhost:11434/v1")
        env_vars["OLLAMA_HOST"] = host

    click.echo()

    # ── Step 3: Channels ──
    click.echo(click.style("Step 3: Channels (optional, press Enter to skip)", bold=True))
    for ch_name, ch_env, ch_prompt in _CHANNELS:
        token = click.prompt(f"  {ch_prompt}", default="", show_default=False)
        if token.strip():
            env_vars[ch_env] = token.strip()
            if ch_name == "feishu":
                secret = click.prompt("  Feishu App Secret", default="", show_default=False)
                if secret.strip():
                    env_vars["FEISHU_APP_SECRET"] = secret.strip()

    click.echo()

    # ── Step 4: Generate files ──
    click.echo(click.style("Step 4: Generate configuration", bold=True))

    # .env
    env_path = _PROJECT_ROOT / ".env"
    if env_path.exists():
        if not click.confirm(f"  .env already exists. Overwrite?", default=False):
            click.echo("  Skipping .env")
        else:
            _write_env(env_path, env_vars)
            click.echo(f"  Written: .env")
    else:
        _write_env(env_path, env_vars)
        click.echo(f"  Written: .env")

    # settings.yaml
    settings_path = _PROJECT_ROOT / "config" / "settings.yaml"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if settings_path.exists():
        if not click.confirm(f"  config/settings.yaml already exists. Overwrite?", default=False):
            click.echo("  Skipping settings.yaml")
        else:
            _write_settings(settings_path, provider_name, env_vars)
            click.echo(f"  Written: config/settings.yaml")
    else:
        _write_settings(settings_path, provider_name, env_vars)
        click.echo(f"  Written: config/settings.yaml")

    click.echo()

    # ── Step 5: LLM connectivity test ──
    if click.confirm("  Run LLM connectivity test?", default=True):
        _test_llm()

    click.echo()
    click.echo(click.style("  Setup complete! Run `gazer start` to launch.", fg="green", bold=True))
    click.echo()


def _write_env(path: Path, env_vars: dict[str, str]) -> None:
    """Write .env file from collected variables."""
    # Read existing .env.example as template if available
    example_path = path.parent / ".env.example"
    lines = []
    if example_path.exists():
        with open(example_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if "=" in stripped and not stripped.startswith("#"):
                    key = stripped.split("=", 1)[0]
                    if key in env_vars:
                        lines.append(f"{key}={env_vars.pop(key)}\n")
                        continue
                lines.append(line)
    # Append any remaining vars
    for k, v in env_vars.items():
        lines.append(f"{k}={v}\n")

    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def _write_settings(path: Path, provider: str, env_vars: dict[str, str]) -> None:
    """Generate a minimal settings.yaml."""
    settings: dict = {
        "personality": {
            "name": "Gazer",
        },
        "security": {
            "dm_policy": "pairing",
        },
    }

    # Enable selected channels
    for ch_name, ch_env, _ in _CHANNELS:
        if ch_env in env_vars:
            settings.setdefault(ch_name, {})["enabled"] = True

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(settings, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _test_llm() -> None:
    """Quick LLM connectivity test."""
    try:
        from dotenv import load_dotenv
        load_dotenv()

        from runtime.config_manager import config
        from llm.router import LLMRouter

        router = LLMRouter(config)
        import asyncio

        async def _ping():
            resp = await router.chat(
                messages=[{"role": "user", "content": "Say OK"}],
                max_tokens=10,
            )
            return resp

        resp = asyncio.run(_ping())
        if hasattr(resp, "error") and resp.error:
            click.echo(click.style(f"  LLM test failed: {resp.content[:80]}", fg="yellow"))
        else:
            click.echo(click.style("  LLM connectivity OK ✓", fg="green"))
    except Exception as exc:
        click.echo(click.style(f"  LLM test failed: {exc}", fg="yellow"))
