# Prompt: build your own browser worker

Drop this into Claude Code or Codex in an empty folder. It builds a tool like the one in
[`browser/`](../browser/): a cheap model drives the browser so your main agent does not.

```text
Build me a small command-line tool that runs one bounded browser task for a coding agent and
returns a short result.

How it works:
- Input: a start URL and a goal (`browse <url> --goal "..."`), or a task file with the goal,
  allowed origins, permissions, limits and checks.
- A loop: read the page as a text snapshot, send a small prompt (goal, current page text, a
  short memory, the last few actions) to a cheap model, get back ONE action as JSON, validate
  it, run it with Playwright CLI, repeat.
- The model has no tools. It only proposes JSON. The tool decides what runs.
- Two models: a cheap worker for the steps, and a stronger decision model that is consulted
  only when the worker says it is stuck or when a check needs a screenshot. Both configurable.
- When the model says it is done, run the checks yourself (visible text, field value, URL,
  or a screenshot judged by the decision model). Never trust "done" without a check.
- Output: one compact JSON result (status, reason, checks, counts, tokens per model). Keep the
  full evidence on disk, not in the result.

Boundaries:
- Allowlist the Playwright CLI commands. No eval, no code execution, no shell.
- Stay inside the allowed origins.
- Refuse controls that submit, buy, delete, publish or send unless the task grants that
  permission. Run such an action at most once and never retry it after an unclear result.
- Cap the number of actions, the seconds and the consultations.
- A private headless browser per task. Use the subscription login of the model CLI, never an API key.

Also write:
- Tests with a fake model and a fake browser: no network and no model calls.
- A short skill file that tells an agent when to use this tool in place of driving the browser itself.
```
