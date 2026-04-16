# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Install

```bash
# Minimal (core only)
pip install -e .

# Full (all optional groups)
pip install -e ".[dev,perception,ui,browser]"
```

### Run

```bash
# Brain + Admin API
python main.py

# CLI interactive REPL
python main.py --cli

# Via installed CLI
gazer start
gazer chat
gazer doctor        # system diagnostics
```

### Test

```bash
# Single targeted test file
pytest -q tests/test_security_regressions.py
pytest -q tests/test_tools_and_skills.py

# Full suite
pytest -q
```

### Lint / Format

```bash
black src/ tests/     # line-length 100, target py310
```

### Frontend (web/)

```bash
cd web && npm install
npm run dev           # dev server proxying localhost:8080
```

### Docker

```bash
docker build -t gazer .
docker compose up -d   # requires .env configured
```

## Architecture

Gazer follows a layered **Runtime + Agent + Tools + Memory + Soul + Devices** architecture.

### Core message flow

```
Channel input
  → ChannelAdapter.publish()
  → MessageBus (src/bus/queue.py)   ← rate-limiting / routing
  → AgentLoop (src/agent/loop.py)   ← context build, LLM call, tool execution
  → MessageBus outbound
  → Channel send
  → Memory / Observability write
```

`AgentLoop` is composed via mixins in `src/agent/loop_mixins/` — each mixin handles a distinct concern (LLM interaction, tool execution, tool policy, planning, channel commands, message processing).

### GazerBrain (`src/runtime/brain.py`)

The top-level orchestrator. On startup it initialises in order: OpenViking memory, `MemoryManager`, `GazerAgent`, `DeviceRegistry`, channels, Admin API (asyncio task, same process), Cron/Heartbeat, perception capture, and hardware layer. The optional face UI (`ui/`) is the only subsystem launched as a separate process.

### Dual-brain execution

`fast_brain` (low-latency responses) and `slow_brain` (deep reasoning) are separate LLM routing paths configured in `config/settings.yaml` under `models.router`.

### Key subsystems

| Subsystem | Location | Purpose |
|---|---|---|
| MessageBus | `src/bus/queue.py` | Decouples channels from agents; all I/O passes through here |
| AgentLoop | `src/agent/loop.py` | LLM call orchestration, tool execution, turn lifecycle |
| ToolRegistry | `src/tools/registry.py` | Central tool registration, policy (Tier) enforcement |
| MemoryManager | `src/memory/` | OpenViking-backed semantic memory; data at `data/openviking/` |
| Soul | `src/soul/` | WorkingContext slots, budget, personality evolution; persona source is `assets/SOUL.md` |
| Security | `src/security/` | OwnerManager, PairingManager, HMAC IPC (`runtime/ipc_secure.py`), log sanitizer |
| LLM Router | `src/llm/` | LiteLLM abstraction, routing strategies, prompt cache |
| Channels | `src/channels/` | Telegram, Discord, Feishu, Slack, Web, WhatsApp, Teams, Signal, Google Chat |
| Devices | `src/devices/` | Local desktop node, body hardware node, hardware serial driver |
| Admin API | `src/tools/admin/` | FastAPI routes; React console in `web/` |

### Configuration

- `config/settings.yaml` — main runtime config (model routing, channels, security, perception, UI)
- `.env` — API keys; at minimum one `{PROVIDER}_API_KEY` is required
- `assets/SOUL.md` — **canonical persona source**; do not duplicate persona injection elsewhere

### Coding rules (from AGENTS.md)

- Channels communicate only through `MessageBus`; no direct channel-to-agent shortcuts.
- Security logic stays in `src/security/*`; do not scatter auth/trust checks.
- Use `logging.getLogger(...)` — no `print()` in runtime code.
- Python 3.10+ with explicit type hints on function signatures.
- On Windows, `asyncio.WindowsSelectorEventLoopPolicy` is applied at startup for compatibility with `python-telegram-bot` and `select()`-based libraries.
