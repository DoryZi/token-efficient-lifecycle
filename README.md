# token-efficient-lifecycle

**Run an AI development lifecycle without burning through your tokens.**

**[The idea](#the-idea)** · [The four tools](#the-four-tools) · [Start here](#start-here) · [Who decides, who drives](#who-decides-who-drives) · [Build your own](#build-your-own)

## The idea

Your strongest model is expensive, and most of what an agent build does is not thinking. It
is reading files, re-reading the plan, running checks and clicking through a browser. So:

```text
STRONG MODEL (your main agent)            CHEAP MODEL (a worker, fresh context)
  decides what to do
  writes a short brief          --->        reads, clicks, writes, runs tests
                                            carries all the heavy context
  reads a short result          <---        returns: status, what changed, evidence
  checks it, decides the next step
```

**The strong model decides. The cheap model drives.** Only short briefs go down and only
short results come back up.

**Cross-harness:** works in **Claude Code** and **Codex**, and either one can hand work to
the other. Runs on your machine with your subscription login. MIT.

⭐ **If this is useful to you, please star the repo.** It is how other people find it.

## The four tools

One for each stage of the lifecycle. Two live in this repo, two have their own.

| Stage | Tool | In one line | Where |
|---|---|---|---|
| **Plan** | plan-html | Settle the plan on a page you click through, before any build cost starts | [DoryZi/plan-html](https://github.com/DoryZi/plan-html) |
| **Build** | delegate | A cheaper model writes the code in its own context | [`orchestrate/`](orchestrate/) |
| **Check** | browser worker | A cheap model drives the browser and returns a short answer | [`browser/`](browser/) |
| **Fix** | bubble-fix | Report issues inside your app, catch errors, fix them in a checked cycle | [DoryZi/bubble-fix](https://github.com/DoryZi/bubble-fix) |

## Start here

**1. Install** (Claude Code):

```text
/plugin marketplace add DoryZi/token-efficient-lifecycle
/plugin install token-efficient-lifecycle@token-efficient-lifecycle
```

<details>
<summary><b>Codex, or a plain clone</b></summary>

```bash
git clone https://github.com/DoryZi/token-efficient-lifecycle ~/token-efficient-lifecycle
mkdir -p ~/.claude/skills ~/.agents/skills
ln -s ~/token-efficient-lifecycle/skills/delegate ~/.claude/skills/delegate   # Claude Code
ln -s ~/token-efficient-lifecycle/skills/browse ~/.claude/skills/browse
ln -s ~/token-efficient-lifecycle/skills/delegate ~/.agents/skills/delegate   # Codex
ln -s ~/token-efficient-lifecycle/skills/browse ~/.agents/skills/browse
```

The skills find the tools through `TOKEN_EFFICIENT_HOME` (default `~/token-efficient-lifecycle`).
If you cloned somewhere else, add `export TOKEN_EFFICIENT_HOME=/your/path` to your shell profile.

</details>

You need Python 3.12 or later, [uv](https://docs.astral.sh/uv/), and Claude Code and/or Codex
logged in. The browser worker also needs [Playwright CLI](https://playwright.dev/docs/getting-started-cli)
with Chromium: `playwright-cli install-browser chromium`.

**2. Tell your agent the rules.** Paste the block from
[prompts/supervision-rules.md](prompts/supervision-rules.md) into your `CLAUDE.md` or
`AGENTS.md`. It tells your agent when to hand work down and when to keep it.

**3. Set up the browser worker once.** It checks your login and probes the two models. Ask
your agent to "set up the browser worker", or run it from the `browser/` folder:

```bash
uv run python browser_worker.py setup --backend claude --config ~/.browser-worker.json
```

**4. Use the two skills.**

```text
/delegate add input validation to the signup form and cover it with tests
/browse http://localhost:3000/signup the form shows an error for a bad email
```

In Codex the same skills are `$delegate` and `$browse`.

Your agent gets back a short result each time: for `/delegate` the worker's final answer and
what it changed, for `/browse` a status with the checks that passed.

## Delegate

Your agent writes a brief, a cheaper model does the work in a fresh context, your agent
checks the result. Nothing is committed, merged or deployed for you.

```text
BRIEF  ->  RUN (cheap model)  ->  READ the short answer  ->  CHECK  ->  retry if needed
```

Step by step, with every command: **[orchestrate/README.md](orchestrate/README.md)**

## Browser worker

A browser throws an enormous amount at your agent: page text, screenshots, a decision on
every click. The browser worker puts the browser in a box. Your agent hands over a page and
a goal and gets back a short answer. It does not analyze each step.

```text
page + goal  ->  THE BOX (cheap model + Playwright CLI)  ->  {status, checks passed, evidence}
```

It will not submit, buy, publish, send or delete unless a task file grants that permission.

Step by step: **[browser/README.md](browser/README.md)**

## Who decides, who drives

| Level | Model | What it does |
|---|---|---|
| **Supervisor** | Your main agent, on the strongest model you have | Sets the goal, chooses what to hand down, makes product and design decisions, reviews the result |
| **Decision** | A mid-priced model, only when asked | Unblocks a stuck worker, judges a screenshot |
| **Worker** | The cheapest model that can do it | Drives the browser, reads pages and logs, takes screenshots, writes boilerplate, runs tests |

- **Hand work down** when the steps are mechanical, a check can tell whether it worked, it
  needs a lot of reading but returns a short answer, and a wrong step is cheap to undo.
- **Keep it up top** when the choice changes what gets built, the requirements are unclear,
  a mistake cannot be undone, or it is the final call on whether the result is good enough.
- **What comes back up is short:** status, what changed, where the evidence is, open questions.

The full rubric, ready to paste: [prompts/supervision-rules.md](prompts/supervision-rules.md)

## Build your own

You do not have to use these tools as they are. These prompts tell your own agent to build
its version, in your stack:

- [Build your own delegate tool](prompts/build-your-own-delegate.md)
- [Build your own browser worker](prompts/build-your-own-browser-worker.md)

## Why it matters

Numbers from the two-day build these tools came out of:

- **Building was the small part.** Only 16% of the agent time went into building. Checking
  took 38%, fixing what the checks found 23%, and orchestration 22%.
- **The plan was read 375 times** by 130 agents. Each read costs a model turn.
- **One browser session made 397 model requests,** with a median of 581K input tokens each.
  About 99% of that input was the same conversation history, read again on every step.

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
