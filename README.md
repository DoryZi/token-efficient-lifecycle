# token-efficient-lifecycle

**Run an AI development lifecycle without burning through your tokens.**

**[The four stages](#the-four-stages)** · [Install](#install) · [Delegate to a cheaper model](#delegate-to-a-cheaper-model) · [Drive the browser cheaply](#drive-the-browser-cheaply) · [Who decides, who drives](#who-decides-who-drives) · [Build your own](#build-your-own)

An agent build spends most of its tokens in places you do not see: re-reading the plan,
checking, fixing what the checks found, and clicking through a browser one screenshot at a
time. This repo is the map of four small tools, one for each stage, that cut that cost. Two of
them live here. Two have their own repos.

**Cross-harness:** the skills work in **Claude Code** and **Codex**, and either one can hand
work to the other. Everything runs on your machine with your subscription login. MIT.

⭐ **If this is useful to you, please star the repo.** It is how other people find it.

## The four stages

| Stage | Tool | What it does | Where |
|---|---|---|---|
| **Plan** | plan-html | You settle the plan on a page you click through, before any build cost starts | [DoryZi/plan-html](https://github.com/DoryZi/plan-html) |
| **Build** | delegate | The strong model plans and reviews. A cheaper model writes the code in its own context | [`orchestrate/`](orchestrate/) in this repo |
| **Check** | browser worker | A cheap model drives the browser through Playwright CLI in small steps and returns a short result | [`browser/`](browser/) in this repo |
| **Fix** | bubble-fix | You report issues inside your app, errors are caught for you, and the agent fixes them in a checked cycle | [DoryZi/bubble-fix](https://github.com/DoryZi/bubble-fix) |

## Why it matters

Numbers from the two-day build these tools came out of:

- **Building was the small part.** Only 16% of the agent time went into building. Checking
  took 38%, fixing what the checks found 23%, and orchestration 22%.
- **The plan was read 375 times** by 130 agents. Each read costs a model turn.
- **One browser session made 397 model requests,** with a median of 581K input tokens each.
  About 99% of that input was the same conversation history, read again on every step.

The fix is the same idea at each stage: keep the expensive model for the thinking, and give
the mechanical work to a cheaper model with a small, fresh context.

## Install

In Claude Code:

```text
/plugin marketplace add DoryZi/token-efficient-lifecycle
/plugin install token-efficient-lifecycle@token-efficient-lifecycle
```

Or clone it (the skills find the tools through `TOKEN_EFFICIENT_HOME`, default
`~/token-efficient-lifecycle`):

```bash
git clone https://github.com/DoryZi/token-efficient-lifecycle ~/token-efficient-lifecycle
ln -s ~/token-efficient-lifecycle/skills/delegate ~/.claude/skills/delegate   # Claude Code
ln -s ~/token-efficient-lifecycle/skills/browse ~/.claude/skills/browse
ln -s ~/token-efficient-lifecycle/skills/delegate ~/.agents/skills/delegate   # Codex
ln -s ~/token-efficient-lifecycle/skills/browse ~/.agents/skills/browse
```

You need Python 3.12 or later, [uv](https://docs.astral.sh/uv/), and Claude Code and/or Codex
logged in. The browser worker also needs [Playwright CLI](https://playwright.dev/docs/getting-started-cli).

## Delegate to a cheaper model

`/delegate <task>` in Claude Code, `$delegate <task>` in Codex. Your agent writes a short
brief, a cheaper model does the work in a fresh context, and your agent reviews the result.

```bash
uv run --directory ~/token-efficient-lifecycle/orchestrate python orchestrate.py \
  --project /path/to/project run fix-login --harness claude --prompt-file /tmp/fix-login.md
```

- **No MCP, no server, no SDK.** It calls the `claude` or `codex` command line you already have.
- **State is saved.** Prompts, logs and the final answer stay in `.orchestrate/<task-id>/`, so
  a failed task can be retried with the same id.
- **Bounded.** A time limit per attempt, a total budget and a maximum number of attempts.
- **Nothing is committed, merged or deployed for you.**

Details: [orchestrate/README.md](orchestrate/README.md).

## Drive the browser cheaply

`/browse <url> <goal>` in Claude Code, `$browse <url> <goal>` in Codex. Use it for any browser
work: checking a page after a change, a smoke test, reading something off a site.

```bash
uv run --directory ~/token-efficient-lifecycle/browser python browser_worker.py \
  browse http://localhost:3000 --goal "Check the sign-up form shows an error for a bad email" --backend claude --read-only
```

- **A cheap model proposes one small step at a time.** It never runs tools itself.
- **Only allowlisted Playwright CLI commands run,** with text results where text is enough.
- **Final actions are refused.** `browse` will not submit, buy, publish, send or delete. A task
  file has to grant that permission explicitly.
- **Your agent gets a short result back,** not the whole browsing session.

Details: [browser/README.md](browser/README.md).

## Who decides, who drives

The rule behind both tools: your strongest model supervises, and the cheapest model that can
do the job drives.

| Level | Model | What it does |
|---|---|---|
| **Supervisor** | Your main agent, on the strongest model you have | Sets the goal, chooses what to hand down, makes product and design decisions, reviews the result |
| **Decision** | A mid-priced model, only when asked | Unblocks a stuck worker, judges a screenshot |
| **Worker** | The cheapest model that can do it | Drives the browser, reads pages and logs, takes screenshots, writes boilerplate, runs tests |

**Hand work down** when the steps are mechanical, a check can tell whether it worked, it
needs a lot of reading but returns a short answer, and a wrong step is cheap to undo.

**Keep it up top** when the choice changes what gets built, the requirements are unclear, a
mistake cannot be undone, or it is the final call on whether the result is good enough.

**What comes back up is short:** status, what changed, where the evidence is, open questions.
Never the whole transcript.

The full rubric is a block you can paste into your `CLAUDE.md` or `AGENTS.md`:
[prompts/supervision-rules.md](prompts/supervision-rules.md).

## Build your own

You do not have to use these tools as they are. Two prompts in [`prompts/`](prompts/) tell
your own agent to build its version, in your stack:

- [Build your own delegate tool](prompts/build-your-own-delegate.md)
- [Build your own browser worker](prompts/build-your-own-browser-worker.md)

## Change the models

| Tool | Claude Code default | Codex default | How to change |
|---|---|---|---|
| delegate | `sonnet` | `gpt-6.1-sol` | `--model` for one run, or `ORCHESTRATE_CLAUDE_MODEL` / `ORCHESTRATE_CODEX_MODEL` |
| browser worker | `haiku` for steps, `sonnet` for decisions | `gpt-6-luna` for steps, `gpt-6.1-sol` for decisions | `setup --worker-model` / `--decision-model`, or `BROWSER_WORKER_MODEL` / `BROWSER_DECISION_MODEL` |

## Status: early release (0.1.0)

Published early, a fuller release is coming. The delegate tool is the one in daily use. The
browser worker is the general core of a larger private tool, cut down and cleaned for this repo.

## Tests

```bash
uv run --directory orchestrate python -m unittest discover -s tests
uv run --directory browser pytest -q
```

## License

MIT, see [LICENSE](LICENSE).

## About

Built by [Dory Zidon](https://doryzidon.com).
Bugs and ideas are welcome in [Issues](https://github.com/DoryZi/token-efficient-lifecycle/issues).
