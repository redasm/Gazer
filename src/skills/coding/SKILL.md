---
name: coding
description: Read, write, and edit code files. Run shell commands. Use git for version control. Development workflows for issues, PRs, and changelogs.
allowed-tools: exec read_file write_file edit_file list_dir find_files git_status git_diff
---

# Coding Workflow

You have access to file system and shell tools for coding tasks.

## Principles

1. **Read before edit**: Always `read_file` first to understand the full context before making changes.
2. **Precise edits**: Use `edit_file` with exact text matches for surgical changes. Use `write_file` only for new files or full rewrites.
3. **Verify after change**: After editing, use `read_file` to confirm the change is correct, or `exec` to run tests.
4. **Stay in workspace**: All file paths are relative to the workspace root.

## File Operations

- `read_file` -- Read file contents with line numbers. Use `offset`/`limit` for large files.
- `write_file` -- Create or overwrite a file. Parent directories are created automatically.
- `edit_file` -- Replace an exact text match. The `old_text` must appear exactly once.
- `list_dir` -- List directory contents. Use `recursive=true` to explore the tree.
- `find_files` -- Glob search, e.g. `**/*.py` to find all Python files.

## Shell Execution

- `exec` -- Run any shell command. Output is captured (stdout + stderr).
- Use for: builds, tests, package installs, linting, formatting.
- Default timeout is 30s. Increase for long builds.

## Git

- `git_status` -- View modified/staged/untracked files.
- `git_diff` -- View working tree changes. Use `file` parameter for a specific file, `staged=true` for staged changes.

## Development Workflows

When the user asks to run a development workflow, load the corresponding prompt file from `prompts/` using `read_file` and follow its instructions.

- **Changelog Audit** (`/cl`) -- Audit changelog entries since the last release. See `prompts/cl.md`.
- **Issue Analysis** (`/is`) -- Analyze GitHub issues: trace root causes for bugs, propose implementation for features. See `prompts/is.md`.
- **Land PR** (`/landpr`) -- End-to-end PR landing: rebase, gate, merge, verify. See `prompts/landpr.md`.
- **Review PR** (`/reviewpr`) -- Structured code review producing a READY / NEEDS WORK recommendation. See `prompts/reviewpr.md`.
