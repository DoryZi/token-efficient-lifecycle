# Agent orchestration

A small command-line tool that either Claude Code or Codex can call through its
shell. No MCP, server, SDK or runtime dependencies. Uses the existing CLI
subscription login. The calling agent decides whether to code directly, delegate,
review, or deliver. There are no mandatory planning or review agents.

From the root of this repository (use an absolute tool path from other projects):

```bash
uv run --directory orchestrate python orchestrate.py --project /path/to/project run fix-login --harness codex --prompt-file /tmp/fix-login.md
uv run --directory orchestrate python orchestrate.py --project /path/to/project status fix-login
uv run --directory orchestrate python orchestrate.py --project /path/to/project wait fix-login --seconds 30
uv run --directory orchestrate python orchestrate.py --project /path/to/project cancel fix-login
```

Use `--harness claude` for Claude Code. Defaults are Sonnet and `gpt-6.1-sol`,
medium effort. The caller can select `--model opus` or `--model gpt-6-astra`
when useful, including on a retry. Either parent can call either CLI.
Set `ORCHESTRATE_CLAUDE_MODEL` or `ORCHESTRATE_CODEX_MODEL` to change a default,
for example `ORCHESTRATE_CLAUDE_MODEL=haiku`; `--model` still wins.

`run` stays in the foreground and emits JSON when the attempt ends. Use the
calling agent's shell background execution for independent work, then `wait`.
Waiting consumes no model calls. Keep the shell job alive; this tool is not a
daemon or a machine-restart service. Parallel tasks need separate working
directories, normally git worktrees. One active task per directory prevents
competing writers. The caller must also avoid editing that directory while a
worker is running. No worktrees, commits, merges or deployments are automatic.

Write a focused prompt containing the task, relevant context file paths, and
acceptance checks. The child reads project instructions normally. Workers are
instructed not to delegate recursively. The skill is [`delegate`](../skills/delegate/SKILL.md): `/delegate <task>` in Claude Code
or `$delegate <task>` in Codex.

## Progress and recovery

State, immutable attempt prompts, raw event logs, stderr and final responses live
in `<project>/.orchestrate/<task-id>/`. Add `.orchestrate/` to other projects'
ignores before using it. Output includes requested model, observed model when
reported, elapsed time and native usage counters. Missing usage stays `null`;
these counters are not subscription billing and differ between providers.

Repeating `run` returns the saved outcome without calling a model, even after a
failure. Inspect the response and logs, then explicitly retry the same task:

```bash
uv run --directory orchestrate python orchestrate.py --project /path/to/project run fix-login --retry --prompt-file /tmp/remaining-fix.md
```

Existing edits and evidence are preserved. Retries start a fresh model context
with pointers to earlier evidence. Omitting `--prompt-file` reuses the last
prompt. Defaults: 20 minutes per attempt, 60 minutes total, three attempts.
Set `--timeout`, `--budget`, `--max-attempts`, and `--max-turns` (Claude only, default 100) on the first run. Limits persist
across retries and cannot be raised by a retry. Use the same task ID for the same
work; a new ID is a new task, not a recovery mechanism. There are no automatic
retries or quota probes. Cancellation and timeouts terminate the child process
group. `SIGTERM`/Ctrl-C save the outcome. A hard-killed supervisor cannot enforce
its deadline; surviving children retain the project lock and must finish or be
stopped by the shell job owner. Once the lock is free, `status` reconciles an
abandoned checkpoint as `interrupted`, counting elapsed time conservatively.

`completed` means the CLI finished with a final answer, **not** that acceptance
tests passed. Read that answer, inspect the work and run the project's verification
workflow. A report format never determines whether product code is accepted.
Exit codes: run success 0, unsuccessful attempt 1, invalid request 2. Status,
wait and cancel return 0 for valid requests; inspect `status` in their JSON.

## Execution boundaries

Codex uses its workspace-write sandbox, no user config, no daemon, and disables
MCP, apps, plugins and native child agents. Claude uses its automatic permission
checks, no interactive prompts, an empty strict MCP configuration and only
built-in file/shell tools. Claude permission checks are not equivalent to the
Codex filesystem sandbox. Run inside the containing environment's sandbox.
Neither adapter bypasses permissions. API credentials and parent-session markers
are excluded from child environments; authentication must report a subscription.
Sandbox or login failures are recorded as failures; permissions are never
automatically loosened. Logs can contain task data, so keep them local.

## Test

```bash
uv run --directory orchestrate python -m unittest discover -s tests -v
```

Tests use local fake CLI processes, including real process-tree cancellation,
locking, deadlines, retries, subscription refusal and evidence persistence.
They do not use subscriptions or network access.
