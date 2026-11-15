# Browser worker

**A browser throws an enormous amount at your agent: page text, screenshots, a new decision on every click. This puts the browser in a box.**

Your strong model does not watch the browser. It hands over a page and a goal, and gets back
a short answer: did it work, which checks passed, where the evidence is. All the reading,
clicking and screenshots happen inside the box, on a cheap model.

## The idea

```text
YOUR AGENT (strong model)          THE BOX (cheap model + Playwright CLI)
  "open this page,          --->     read page as text
   check the sign-up                 propose one small step
   form rejects a bad                run it (only allowed commands)
   email"                            repeat, up to a step limit
                                     check the goal itself
  short result              <---   {status, checks passed, evidence path}
  decide what to do next
```

- **Your agent decides. The box drives.** The strong model never analyzes each step.
- **Each step is small.** The cheap model sees the goal, the current page text and its last
  few actions. No long conversation is read again on every click.
- **Text first.** Pages are read as text. A screenshot is taken only when a check needs one.
- **Two models.** A cheap *worker* model does the steps. A stronger *decision* model is asked
  only when the worker is stuck or a check needs a screenshot judged. Each such ask is a
  *consultation*, and they are capped.
- **It checks its own work.** When the cheap model says "done", the tool runs the checks. A
  claim of success is never taken on trust.
- **It hands back when stuck.** A blocked run returns the reason, so your agent can decide.

## Use it

Your agent runs this for you once the [rules block](../prompts/supervision-rules.md) is in
your `CLAUDE.md` or `AGENTS.md`. To run it by hand, from this folder:

**1. Set it up once.** It checks your login and sends one tiny probe to each of the two models,
so it uses a few subscription calls:

```
uv run python browser_worker.py setup --backend claude
```

The choice is saved in `.browser-worker.json` in this folder. To keep it in your home folder,
add `--config ~/.browser-worker.json` to `setup` and to every `browse` and `run`. The rules
block uses that location.

**2. Give it a page and a goal:**

```
uv run python browser_worker.py browse https://example.com \
  --goal "Confirm the page shows the heading Example Domain" \
  --backend claude --read-only
```

**3. Read the short result.** It is one small JSON object:

| Field | Meaning |
|---|---|
| `status` | `completed` or `blocked` |
| `reason` | Why it stopped |
| `checks` | Each check with `passed` true or false |
| `actions`, `consultations`, `seconds` | How much work it took |
| `models` | Calls and tokens per model |
| `evidence`, `details` | Where the full record and screenshots are on disk |

State the goal as something that can be **seen on the page** when it is reached. "Check the
form" is vague. "The form shows an error under the email field" can be checked.

It uses your Claude Code or Codex subscription login. It never uses an API key.

## Install

You need:

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- `playwright-cli` on your PATH, with Chromium installed: `playwright-cli install-browser chromium`
- Claude Code or Codex, logged in with your subscription

Run commands from this folder with `uv run`.

## Browse options

The full command:

```
uv run python browser_worker.py browse https://example.com \
  --goal "Confirm the page shows the heading Example Domain" \
  --backend claude --read-only --max-steps 20
```

Options:

- `--goal` what to do. Required.
- `--backend claude|codex`. Required.
- `--read-only` allow reading only. No typing or clicking that edits.
- `--max-steps N` cap on browser actions (1 to 300, default 60). Every `playwright-cli` call
  counts, including reading the page. One click costs about 4 to 5 calls, so 60 is roughly
  12 to 15 clicks. Use 100 or more for goals with many steps.
- `--mode mixed|cheap|strong` which models do the steps. `mixed` (default): the cheap worker
  model does the steps and the stronger decision model is consulted when the worker is stuck.
  `strong`: the decision model does every step. `cheap`: never consult the decision model.
  `browse` needs the decision model to judge its final screenshot, so do not use `cheap` with it.
- `--out DIR` where to keep evidence. The folder must not exist yet. Use an absolute path:
  with `uv run --directory`, relative paths are read from the tool folder, not your project.
- `--config FILE` where the saved setup is. Default `.browser-worker.json` in this folder.

`browse` builds a task in code. Its only check is a visual one: the decision model
looks at a final screenshot and confirms the goal. For exact checks, use a task file.

## Run a task file

```
uv run python browser_worker.py run --task-file examples/check-page.json --backend claude
```

From another folder, give the task file as an absolute path:

```
uv run --directory /path/to/browser python browser_worker.py run --task-file /abs/path/task.json --backend claude
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

The command prints a compact JSON result: `status`, `reason`, `backend`, `seconds`, `actions`,
`consultations`, `cleanup_error`, `checks`, `models`, `evidence` and `details`. Status is
`completed` or `blocked`. Each check has only `name` and `passed`; the actual values are in
`result.json` in the evidence folder. Exit code is 0 for completed and 2 for anything else,
including a missing setup. Without `--out`, evidence goes to `runs/<id>/` in this folder.

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
