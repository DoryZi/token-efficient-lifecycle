# Delegate

**Your strong model writes a short brief. A cheaper model does the work in its own fresh context. Your strong model checks the result.**

That is the whole idea. The cheap model reads the files, runs the tests and makes the edits, so
none of that fills up the expensive model's context.

One small Python file, `orchestrate.py`. No server, no MCP, no SDK, no dependencies. It calls
the `claude` or `codex` command line you already have, with your subscription login.

## The loop

```text
1. BRIEF    your agent writes a prompt file: the task, the files that matter, the checks
2. RUN      a cheaper model does the work in a fresh context
3. READ     your agent reads the short final answer
4. CHECK    your agent looks at the changes and runs the project's tests
            not right? write a new brief and RETRY the same task
```

## Use it

Your agent runs this for you once the [rules block](../prompts/supervision-rules.md) is in
your `CLAUDE.md` or `AGENTS.md`. To run it by hand:

**1. Write a brief.** Save it as a file, for example `/tmp/fix-login.md`:

```text
Fix the login form: it accepts an empty password.
Files: app/auth.py, tests/test_auth.py
Done when: `pytest tests/test_auth.py` passes and a new test covers the empty password.
Do not touch other files.
```

**2. Run it.**

```bash
uv run --directory orchestrate python orchestrate.py --project /path/to/project \
  run fix-login --harness claude --prompt-file /tmp/fix-login.md
```

`fix-login` is the task id. You choose it. `--harness` is who does the work: `claude` or `codex`.

**3. Read the result.** `run` prints the task's state as JSON when the worker ends:

```json
{
  "task": "fix-login",
  "status": "completed",
  "attempts": [
    {"number": 1, "harness": "claude", "model": "sonnet", "status": "completed",
     "seconds": 214, "usage": {"output_tokens": 14481},
     "response": "/path/to/project/.orchestrate/fix-login/1.response.md"}
  ]
}
```

The worker's answer is the file in the last attempt's `response`. Read that, not the log.

**4. Check the work yourself.** `completed` means the worker finished. It does not mean the
work is right. Look at the diff and run your tests.

**Not right?** Retry the same task with a new brief. The worker starts fresh and is pointed at
what the earlier attempt left behind. Without `--prompt-file`, a retry repeats the last prompt:

```bash
uv run --directory orchestrate python orchestrate.py --project /path/to/project \
  run fix-login --retry --prompt-file /tmp/fix-login-2.md
```

## All commands

| Command | What it does |
|---|---|
| `run <task-id> --harness claude\|codex --prompt-file FILE` | Start the worker and wait for it. Prints JSON at the end |
| `run <task-id> --retry [--prompt-file FILE]` | A new attempt on the same task, in a fresh context |
| `status <task-id>` | The saved state of a task |
| `wait <task-id> --seconds 30` | Wait up to that long (1 to 60 seconds) and print the state. Call it again while the status is `running`. Uses no model calls |
| `cancel <task-id>` | Stop a running task |

Every command requires `--project DIR`, the folder the worker works in, placed **before** the
command name: `orchestrate.py --project DIR run ...`.

To do other things while a worker runs, start `run` in the background of your shell and use `wait`.

## Which model

| Harness | Default | Change it for one run | Change the default |
|---|---|---|---|
| `claude` | `sonnet` | `--model haiku`, `--model opus` | `ORCHESTRATE_CLAUDE_MODEL=haiku` |
| `codex` | `gpt-6.1-sol` | `--model gpt-6-astra` | `ORCHESTRATE_CODEX_MODEL=...` |

`--effort low|medium|high` sets how hard the model thinks. The default is medium.

## Limits

Set these on the first `run`. They stay in force across retries and a retry cannot raise them.

| Flag | Default | Meaning |
|---|---|---|
| `--timeout` | 1200 | Seconds per attempt |
| `--budget` | 3600 | Total seconds across all attempts |
| `--max-attempts` | 3 | Total attempts |
| `--max-turns` | 100 | Model turns per attempt (Claude only) |

## What is saved, and where

Everything for a task is in `<project>/.orchestrate/<task-id>/`: `state.json`, and for each
attempt its prompt, the raw event log, stderr and the final answer. Add `.orchestrate/` to the project's
`.gitignore`. Logs can contain task data, so keep them local.

Running `run` again on a finished task returns the saved outcome and calls no model.

## Rules it keeps

- **One task per folder at a time.** For parallel tasks use separate folders, normally git
  worktrees. Do not edit a folder while its worker is running.
- **It never commits, merges, pushes or deploys.**
- **Subscription login only.** API keys are removed from the worker's environment. If the CLI
  is not logged in with a subscription, the run fails with a clear message.
- **The worker gets no MCP servers, no plugins and no child agents.**
- **No hidden retries.** A failed attempt stays failed until you retry it.
- **Not a sandbox.** Codex runs in its workspace-write sandbox. Claude runs with its automatic
  permission checks, which are not a filesystem sandbox. Run it where you would run the CLI itself.

Exit codes: 0 for a successful run, 1 for an unsuccessful attempt, 2 for an invalid request.

## Tests

```bash
uv run --directory orchestrate python -m unittest discover -s tests -v
```

The tests use fake `claude` and `codex` programs. They need no subscription and no network.
