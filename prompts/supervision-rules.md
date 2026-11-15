# Who decides, who drives

Paste this block into your `CLAUDE.md` or `AGENTS.md`. It tells your main agent how to split
work between itself and cheaper models, and makes the two tools the required way to do browser
work and mechanical build work. Rules in these files are read on every task, so the agent
uses the tools every time, not only when it remembers to.

```markdown
## Model use: who decides, who drives

You are the supervisor. You run on the strongest model. Your tokens are the expensive ones,
so spend them on decisions, not on driving.

Three levels:
- **Supervisor (you, the strongest model):** set the goal, choose what to hand down, make
  product and design decisions, review results, decide when the work is done.
- **Decision model (mid-priced, only when asked):** unblock a stuck worker, judge a screenshot.
- **Worker (the cheapest model that can do it):** drive the browser, read pages and logs,
  take screenshots, write boilerplate, run tests. It carries the heavy context so you do not.

Hand work down when all of these are true:
- The steps are known or mechanical.
- A test or a clear check can tell whether it worked.
- It needs a lot of reading or clicking but returns a short answer.
- A wrong step is cheap to undo.

Keep work for yourself when any of these is true:
- The choice changes what gets built (product, design, scope).
- The requirements are unclear and need a question to the user.
- A mistake is costly or cannot be undone (deploy, delete, send, publish, pay).
- It is the final judgment on whether the result is good enough.

A worker hands back up when:
- It failed the same step twice.
- It meets a choice the brief did not cover.
- A check fails and the fix is not obvious.

What comes back up is short: status, what changed, where the evidence is, open questions.
Never the whole transcript, never raw page dumps, never screenshots unless you ask for one.

Before handing down, write a brief: the goal, the files or pages that matter, what is out of
scope, and the checks that say it is done. The worker starts with no memory of this conversation.

### The tools for this (mandatory)

The tools are in `~/token-efficient-lifecycle`. Read a tool's README once before first use.

- **Browser work: never drive the browser yourself step by step.** Hand it to the browser worker:

  `uv run --directory ~/token-efficient-lifecycle/browser python browser_worker.py browse <url> --goal "<what must be visible on the page when done>" --backend claude --read-only --config "$HOME/.browser-worker.json"`

  Leave out `--read-only` when it has to type or click to change something. Read the short
  JSON result. Open a screenshot from its evidence folder only when you need to see it yourself.
  Exceptions: the user asks you to look yourself, or the page needs the user's own login
  (the worker runs a private browser and cannot reach it).

- **Mechanical build work (boilerplate, tests, small fixes): delegate it.** Write the brief to a
  file, then:

  `uv run --directory ~/token-efficient-lifecycle/orchestrate python orchestrate.py --project <dir> run <task-id> --harness claude --prompt-file <brief-file>`

  Read the worker's answer (the `response` file of the last attempt), look at the diff and run
  the tests yourself. `completed` means it finished, not that it is right.

- Use `--backend codex` and `--harness codex` to have the Codex models do the work.
```

If you cloned the repo somewhere else, change `~/token-efficient-lifecycle` in the block.
