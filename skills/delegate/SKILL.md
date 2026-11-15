---
name: delegate
description: Hand a bounded task (a build step, a fix, a test run) to a cheaper model in its own context, through the Claude Code or Codex command line. Use when the user says "delegate this", "use a cheaper model", "run this in another context", or when a task is mechanical and does not need the strongest model. Works in Claude Code (/delegate) and Codex ($delegate).
---

# Delegate

Invoke `/delegate <task>` in Claude Code or `$delegate <task>` in Codex.

You own the plan and the review. A cheaper model does the work in a fresh context, so your own
context does not fill up with its file reads and tool output.

The tool lives in this plugin's `orchestrate/` folder. Use this path as `<tool>`:

```text
${CLAUDE_PLUGIN_ROOT:-${TOKEN_EFFICIENT_HOME:-$HOME/token-efficient-lifecycle}}/orchestrate
```

## How

1. Write a prompt file with the task, the paths of the files that matter, and the checks that
   say it is done. Keep it focused: the worker starts with no memory of this conversation.
2. Start the worker. Pick the harness you want to do the work; either parent can call either one.

   ```bash
   uv run --directory <tool> python orchestrate.py --project <working-directory> run <task-id> --harness claude --prompt-file <prompt-file>
   ```

   `run` stays in the foreground and prints JSON when the attempt ends. Run it in the
   background when you have other work, then use `wait`:

   ```bash
   uv run --directory <tool> python orchestrate.py --project <working-directory> wait <task-id> --seconds 30
   uv run --directory <tool> python orchestrate.py --project <working-directory> status <task-id>
   uv run --directory <tool> python orchestrate.py --project <working-directory> cancel <task-id>
   ```

3. Read the worker's final answer, look at what it changed and run the project's own checks.
   `completed` means the worker finished, not that the work is right.
4. To continue the same work after a failure, retry with the same task id:

   ```bash
   uv run --directory <tool> python orchestrate.py --project <working-directory> run <task-id> --retry --prompt-file <new-prompt-file>
   ```

## Which model

Defaults: `sonnet` for Claude Code and `gpt-6.1-sol` for Codex, medium effort.

- One run: `--model haiku`, `--model opus`, `--effort low|medium|high`.
- Every run: set `ORCHESTRATE_CLAUDE_MODEL` or `ORCHESTRATE_CODEX_MODEL`. `--model` still wins.

Use the cheaper model for mechanical work (boilerplate, tests, small fixes). Keep the strong
model for planning, hard design choices and the final review.

## Rules

- One active task per working directory. Parallel tasks need separate directories, normally
  git worktrees. Do not edit a directory while its worker is running.
- Nothing is committed, merged or deployed for you.
- Workers use your subscription login. API keys are never passed to them.
- Add `.orchestrate/` to the project's `.gitignore`: state and logs are kept there.
