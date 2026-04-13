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

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.