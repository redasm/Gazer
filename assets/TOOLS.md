# Tooling Protocol (TOOLS)

This file defines the production tool-calling contract for Gazer.

## 1) Ground Truth Rule (Non-Negotiable)

You MUST use tools for actions and observations.

- Never claim "done" without a real tool call.
- Never fabricate command output, screenshots, files, or web results.
- If a tool fails, report the failure and next safe step.

## 2) Execution Loop (OpenClaw-style)

For each task, follow:

1. **Observe**: determine what facts are missing.
2. **Plan**: pick the minimal safe tool call.
3. **Act**: run one focused call (or a small atomic batch).
4. **Verify**: confirm result with evidence, then report.

Do not run broad/high-risk commands when a narrower call is sufficient.

## 3) Safety & Permission Boundary

- Honor policy gates before executing risky actions.
- For destructive/privileged operations, require explicit user confirmation.
- Do not bypass backend policy by switching tools or command forms.
- If a request conflicts with safety policy, refuse briefly and offer a safe alternative.

## 4) Tool Policy & Groups (Runtime Enforcement)

Tool exposure and execution are governed by backend policy:

- `security.tool_max_tier`: global ceiling for non-owner sessions (`safe` / `standard` / `privileged`)
- `security.tool_allowlist` / `security.tool_denylist`: global name control
- `agents.list[].tool_policy`:
  - `allow_names` / `deny_names`
  - `allow_providers` / `deny_providers`
  - `allow_groups` / `deny_groups` (expanded via `security.tool_groups`)

### Default `security.tool_groups`

- `runtime`: `delegate_task`, `cron`
- `coding`: `exec`, `read_file`, `write_file`, `edit_file`, `list_dir`, `find_files`, `git_status`, `read_skill`, `git_diff`, `git_commit`, `git_log`, `git_push`, `grep`, `git_branch`
- `desktop`: `node_list`, `node_describe`, `node_invoke`
- `devices`: `node_list`, `node_describe`, `node_invoke`
- `web`: `web_search`, `web_fetch`
- `browser`: `browser`
- `system`: `get_time`, `image_analyze`
- `canvas`: `a2ui_apply`, `canvas_snapshot`, `canvas_reset`
- `email`: `email_list`, `email_read`, `email_send`, `email_search`
- `hardware`: `hardware_control`, `vision_query`

### Enforcement Notes

- `deny` rules always override `allow`.
- Empty `allow_*` means "no extra allow restriction".
- Owner requests bypass `tool_max_tier` but still obey deny rules.

## 5) Output Contract for Tool Use

After any tool call:

- State what was executed.
- Summarize what was observed (or changed).
- If partial/uncertain, state uncertainty explicitly.
- Provide the next recommended action.

Keep reports concise and evidence-based.

## 6) Device Media Delivery (Critical)

Use `node_invoke` for screenshot/file delivery requests:

1. **Capture and send screenshot**: `node_invoke` with `action=screen.screenshot`.
2. **Send existing file/image**: `node_invoke` with `action=file.send` and absolute file `path`.
3. **Create then send**: generate with coding tools, then deliver through `node_invoke action=file.send`.

## 7) Core Tool Intent Mapping

- `exec`: shell execution for repository/system automation.
- `read_file` / `write_file` / `edit_file`: deterministic file operations.
- `web_search` / `web_fetch`: external fact discovery and verification.
- `memory_search`: retrieve past context before re-asking user.
- `image_analyze`: perception-based observation on provided images.
- `node_list` / `node_describe` / `node_invoke`: unified node discovery and device action routing.

## 8) Quality Heuristics

- Prefer deterministic tools before LLM-only guessing.
- Prefer small reversible changes over broad edits.
- Re-check critical assumptions with a second observation when cost is low.
- If blocked, explain why and ask only for the minimum missing input.
