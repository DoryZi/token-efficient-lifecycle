# Browser worker

A small, bounded browser task runner for agents.

The calling agent gives it a page and a goal. A cheap model looks at the page and
proposes one small JSON action at a time. Only allowlisted `playwright-cli` commands
run. Final actions (submit, buy, delete, publish, send) are refused unless the task
explicitly allows them. A short result comes back.

It uses your Claude Code or Codex subscription login. It never uses an API key and
never falls back to paid API billing.

## Why it saves tokens

- Each step uses a small, fresh context: the task, the current page text, a short memory and the last few actions. There is no long chat history to re-read.
- A cheap worker model does most steps. A stronger decision model is used only when the worker is stuck or a visual check needs a screenshot.
- Pages are read as text snapshots. Screenshots are used only when needed.
- Steps, time and decision-model consultations are all capped.
- The calling agent gets a compact result, not the whole browsing session. Full evidence stays on disk.

## Install

You need:

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- `playwright-cli` on your PATH, with a Chromium browser installed
- Claude Code or Codex, logged in with your subscription

Run commands from this folder with `uv run`.

## Setup

Run once per backend. It checks your login and sends one tiny probe to each model.

```
uv run python browser_worker.py setup --backend claude
uv run python browser_worker.py setup --backend codex
```

The choice is saved in `.browser-worker.json` (ignored by git).

## Browse

Open a page and work toward a goal:

```
uv run python browser_worker.py browse https://example.com \
  --goal "Confirm the page shows the heading Example Domain" \
  --backend claude --read-only --max-steps 20
```

Options:

- `--goal` what to do. Required.
- `--backend claude|codex`. Required.
- `--read-only` allow reading only. No typing or clicking that edits.
- `--max-steps N` cap on browser actions (1 to 300, default 60).
- `--mode mixed|cheap|strong` model use. `mixed` is the default.
- `--out DIR` where to keep evidence. The folder must not exist yet.

`browse` builds a task in code. Its only check is a visual one: the decision model
looks at a final screenshot and confirms the goal. For exact checks, use a task file.

## Run a task file

```
uv run python browser_worker.py run --task-file examples/check-page.json --backend claude
```

### Task fields

- `objective` (required): what to do.
- `url` (required): the start page. Only http and https, no credentials in the URL.
- `checks` (required): a list of checks run on the page after the model says it is done. Each has `name`, `kind` and `expected`.
  - `text`: the element's visible text contains `expected`.
  - `value`: the element's value equals `expected`.
  - `checked`: the element's checked state equals `expected` (true or false).
  - `url`: the page URL equals `expected`.
  - `visual`: the decision model confirms it from a screenshot.
  - `text`, `value` and `checked` take an optional `selector` (CSS). The default is `body`.
- `facts`: values the model may use, such as text to type. Keep it small.
- `permissions`: any of `read`, `edit`, `submit`, `delete`. Default is `read` and `edit`.
- `allowed_origins`: pages the task may open. Default is the start URL's origin.
- `attachments`: files that may be uploaded, relative to the task file.
- `max_actions` (default 60, max 300), `max_seconds` (default 600, max 3600), `max_consultations` (default 3, max 10).

The whole task must stay under 10,000 characters.

## Result

The command prints a compact JSON result: status, reason, checks, counts and token use
per model. Status is `completed` or `blocked`. Exit code is 0 for completed and 2
otherwise. Full details are in `result.json` inside the evidence folder. Default
evidence goes to `runs/<id>/`.

## Safety boundaries

- Only allowlisted `playwright-cli` commands run. No `eval`, no `run-code`, no shell.
- Arguments are checked. Text typed into a page is passed literally.
- Navigation stays inside `allowed_origins`.
- Controls that look like submit, buy, delete, publish or send are refused unless the task has the matching permission. Pressing Enter in a form is refused too.
- A final action runs at most once and is never retried after an unclear result.
- Publishing is never done by this tool.
- Uploads must be files listed in `attachments`.
- Each task runs in its own private, headless Chromium. Attaching to a browser you already have open is not supported. Task files with `attach`, `session` or `profile_directory` are rejected.
- Model processes run with no tools, no MCP servers and no project instructions.
- Auth files are not copied into the evidence folder.

## Change the models

Defaults:

| Backend | Worker | Decision |
| --- | --- | --- |
| claude | haiku | sonnet |
| codex | gpt-6-luna | gpt-6.1-sol |

Save other models with setup:

```
uv run python browser_worker.py setup --backend claude --worker-model haiku --decision-model sonnet
```

Or override for one run with environment variables. They win over the saved config:

```
BROWSER_WORKER_MODEL=haiku BROWSER_DECISION_MODEL=sonnet \
  uv run python browser_worker.py browse https://example.com --goal "..." --backend claude
```

## Tests

```
uv run pytest -q
```

Tests use fake models and a fake browser. They make no network calls and no model calls.
`tests/fixture.html` is a small page for manual runs. Serve it locally, for example with
`python3 -m http.server --bind 127.0.0.1` from the `tests` folder.
