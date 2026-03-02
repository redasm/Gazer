---
name: desktop
description: Observe and interact with desktop nodes through the unified node tool interface.
allowed-tools: node_list node_describe node_invoke
---

# Desktop Interaction

Use node tools to observe and control desktop targets.

## Workflow

1. **Discover target**: Use `node_list` to find available nodes.
2. **Inspect capabilities**: Use `node_describe` for supported actions.
3. **Invoke safely**: Use `node_invoke` with explicit `action` and `args`.
4. **Verify**: Re-run observation actions after control actions.

## Safety

- Prefer passive observation over active control.
- For sensitive operations (sending messages, deleting files via UI), ask for user confirmation.
- Do not rapid-fire input actions; wait and observe between actions.

## Tools

- `node_list` -- List available nodes and default target.
- `node_describe` -- Show capabilities for one node.
- `node_invoke` -- Execute actions such as:
  - `screen.observe` with `{"query": "..."}`
  - `screen.screenshot` with `{}`
  - `input.mouse.click` with `{"x": 100, "y": 200, "button": "left"}`
  - `input.keyboard.type` with `{"text": "hello"}`
  - `input.keyboard.hotkey` with `{"keys": ["ctrl", "s"]}`
  - `file.send` with `{"path": "C:/path/to/file.png"}`
