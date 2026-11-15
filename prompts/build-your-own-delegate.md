# Prompt: build your own delegate tool

Drop this into Claude Code or Codex in an empty folder. It builds a tool like the one in
[`orchestrate/`](../orchestrate/), in your own stack and to your own taste.

```text
Build me a small command-line tool that lets a coding agent hand a task to a cheaper model
running in its own fresh context.

What it must do:
- `run <task-id> --project <dir> --harness claude|codex --prompt-file <file> [--model M] [--effort low|medium|high]`:
  start the `claude` or `codex` command line headless in <dir>, feed it the prompt on stdin,
  wait, and print one JSON result (status, model, elapsed seconds, token counters when the
  CLI reports them, path of the final answer).
- `status`, `wait --seconds N` and `cancel` for a task id.
- Save everything under `<dir>/.orchestrate/<task-id>/`: the prompt of each attempt, the raw
  event log, stderr and the final answer. Repeating `run` returns the saved outcome without
  calling a model. `run --retry` starts a new attempt with a fresh context.
- Default models: a cheap one per harness, changeable by a flag and by an environment variable.
- Limits that persist across retries: seconds per attempt, total seconds, maximum attempts.

Boundaries:
- One active task per directory, enforced with a lock file.
- Use the subscription login of the CLI. Remove API keys from the child's environment and
  fail clearly if the CLI is not logged in.
- No MCP servers, no plugins, no child agents inside the worker.
- Never commit, merge, push or deploy.
- No daemon and no runtime dependencies beyond the standard library.

Also write:
- Tests that use a fake `claude` and `codex` executable, so they need no network and no model.
- A short rules block for my CLAUDE.md or AGENTS.md that tells the agent when and how to use the tool.

Ask me before choosing the language if this folder does not make it obvious.
```
