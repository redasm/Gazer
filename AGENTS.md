# AGENTS.md

Repository-wide guidance for coding agents in **Gazer**.

## Scope

- This file applies to the full repository.
- If a deeper directory has another `AGENTS.md`, the deeper file overrides this one for that subtree.

## Project Overview

Gazer is a desktop embodied AI companion.

- Backend: Python (`src/*`) for agent loop, memory, tools, channels, security, eval.
- Frontend: React/Vite (`web/`) for admin and chat UI.
- Runtime assets: `assets/*`.
- Tests: `tests/*` (pytest).

## Architecture Rules

- Channels communicate through `MessageBus`; do not add direct channel-to-agent shortcuts.
- `agent` orchestrates; `tools/llm/memory/soul` provide capabilities; `runtime` wires lifecycle.
- Keep security logic centralized in `src/security/*`.
- Keep memory as OpenViking-backed runtime data under `data/openviking`.
- `assets/SOUL.md` is the canonical soul source; avoid duplicate persona sources.

## Coding Standards

- Python 3.10+ with explicit type hints for function signatures.
- Keep diffs focused and root-cause oriented.
- Prefer deterministic behavior and observable logs for critical flows.
- Use `logging.getLogger(...)`; avoid `print()` in runtime code.
- Keep text files UTF-8 and use ASCII where possible in code/comments.

## Test Expectations

- Run targeted tests first for touched modules, then broader regression if needed.
- Do not claim behavior changes without test evidence.
- For memory/tooling/runtime changes, include at least one regression test.

## Safety & Ops

- Never claim an action ran unless it actually ran.
- Do not expose secrets from `.env` or provider keys.
- Ask before destructive operations.
- Avoid broad/deceptive fallbacks that hide root cause.

## Prompt & Persona

- Runtime/prompt composition should stay aligned:
  - Prompt context from `assets/AGENTS.md`, `assets/TOOLS.md`, optional `assets/USER.md`, optional `assets/IDENTITY.md`.
  - Persona from canonical `assets/SOUL.md`.
- Avoid duplicate persona injection paths that drift over time.

## Delivery Format

For significant changes, include:

- What changed and why.
- Risk and impacted modules.
- Test commands and results.
