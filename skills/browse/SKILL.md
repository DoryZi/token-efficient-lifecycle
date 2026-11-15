---
name: browse
description: Hand a browser task to a cheap model that drives Playwright CLI in small bounded steps and returns a short result. Use for any browser work that does not need your own eyes on every step, such as checking a page after a change, a smoke test, confirming text or a form state, or reading something off a site. Use when the user says "check it in the browser", "test the page", "browse to", or when you are about to take many screenshots yourself. Works in Claude Code (/browse) and Codex ($browse).
---

# Browse

Invoke `/browse <url> <goal>` in Claude Code or `$browse <url> <goal>` in Codex.

Driving a browser yourself is the most expensive way to do it: every click makes you read
your whole conversation again. This tool gives the driving to a cheap model with a small,
fresh context per step. You keep the decisions and get a short result back.

The tool lives in this plugin's `browser/` folder. Use this path as `<tool>`:

```text
${CLAUDE_PLUGIN_ROOT:-${TOKEN_EFFICIENT_HOME:-$HOME/token-efficient-lifecycle}}/browser
```

## How

1. Once per machine and backend:

   ```bash
   uv run --directory <tool> python browser_worker.py setup --backend claude --config "$HOME/.browser-worker.json"
   ```

   It makes a few small subscription calls to probe the models. Skip it when
   `~/.browser-worker.json` already has this backend.

2. State the goal as something that can be seen on the page when it is reached, then run:

   ```bash
   uv run --directory <tool> python browser_worker.py browse <url> --goal "<goal>" --backend claude --read-only --config "$HOME/.browser-worker.json"
   ```

   Always pass `--config` as above. Paths are read from the tool folder, so give `--out` and
   `--task-file` as absolute paths.

   - `--read-only` for checks. Leave it out when the task has to type or click to change something.
   - `--max-steps N` caps browser actions (default 60). Every Playwright CLI call counts, and
     one click costs about 4 to 5, so 60 is roughly 12 to 15 clicks. Use 100 or more for long goals.
   - Do not use `--mode cheap` with `browse`: its final check needs the decision model.
   - `--backend codex` uses the Codex models.

3. Read the JSON result: `status` is `completed` or `blocked`, with the reason and the checks.
   The full evidence is in the run folder it names. Open a screenshot from there only when you
   need to see it yourself.
4. For exact checks (a field value, a URL, text inside one element), write a task file and
   use `run --task-file`. The fields are in the tool's README.

## Who decides, who drives

- **You decide:** what to check, whether the result is good enough, anything that changes the product.
- **The worker drives:** opening pages, reading them, clicking, typing, screenshots.
- **It hands back to you** when it is blocked. Do not just raise the step limit and retry:
  read the reason, then give it a clearer goal or decide yourself.

## Boundaries

- It will not submit, buy, publish, send or delete. `browse` never grants those permissions.
- It stays on the start URL's site.
- It runs its own private headless browser. It cannot use a browser you already have open,
  so it cannot reach pages that need your login.
- Models: `haiku` for steps and `sonnet` for decisions by default (`gpt-6-luna` and
  `gpt-6.1-sol` on Codex). Change them with `setup --worker-model ... --decision-model ...`
  or `BROWSER_WORKER_MODEL` / `BROWSER_DECISION_MODEL`. To put your strongest model on the
  decisions, set the decision model to it.
