"""Run one agent task. The caller owns planning, verification and delivery."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time
import uuid

DEFAULT_MODELS = {"codex": "gpt-6.1-sol", "claude": "sonnet"}
MODEL_ENV = {"codex": "ORCHESTRATE_CODEX_MODEL", "claude": "ORCHESTRATE_CLAUDE_MODEL"}
PASS_ENV = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TZ", "TMPDIR",
    "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
    "CODEX_HOME", "CLAUDE_CONFIG_DIR", "UV_CACHE_DIR", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "NODE_EXTRA_CA_CERTS", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
}


def resolve(value: str) -> Path:
    path = Path(value).expanduser()
    return (Path(os.environ.get("PWD", os.getcwd())) / path).resolve()


def child_env() -> dict[str, str]:
    # Do not pass API keys, provider overrides or nested-session markers.
    return {k: v for k, v in os.environ.items() if k in PASS_ENV or k.startswith("LC_")}


def save(path: Path, value: dict) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x") as stream:
            os.chmod(temp, 0o600)
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def project_lock(root: Path):
    with (root / ".orchestrate" / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("An agent task is already running here; use a separate worktree for parallel work") from None
        try:
            yield lock
        finally:
            # Close rather than LOCK_UN: an orphan child retains the inherited lock.
            pass


def paths(root: Path, task: str) -> tuple[Path, Path]:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", task):
        raise ValueError("Task ID must be 1-80 letters, digits, hyphens or underscores")
    folder = root / ".orchestrate" / task
    return folder, folder / "state.json"


def read_state(root: Path, task: str) -> dict:
    folder, path = paths(root, task)
    state = json.loads(path.read_text())
    if state["status"] == "running":
        try:
            with project_lock(root):
                # Re-read after acquiring the lock: the worker may just have finished.
                state = json.loads(path.read_text())
                if state["status"] == "running":
                    attempt = state["attempts"][-1]
                    attempt.update(status="interrupted", ended_at=time.time(),
                                   error="Worker exited without a final checkpoint; inspect saved logs before retrying")
                    attempt["seconds"] = max(0, attempt["ended_at"] - attempt["started_at"])
                    state["status"] = "interrupted"
                    save(path, state)
        except ValueError:
            pass  # A live worker (or its child) still holds the project lock.
    return state


def subscription(harness: str, env: dict, timeout: float, root: Path) -> None:
    command = ["codex", "login", "status"] if harness == "codex" else ["claude", "auth", "status", "--json"]
    result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=min(20, timeout))
    if harness == "codex":
        text = (result.stdout + result.stderr).lower()
        valid = "logged in using chatgpt" in text and "api key" not in text
    else:
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            data = {}
        valid = data.get("loggedIn") is True and data.get("authMethod") in {"claude.ai", "oauth"}
    if result.returncode or not valid:
        raise ValueError(f"{harness}: subscription login required; API authentication is not supported")


def command(harness: str, model: str, effort: str, max_turns: int = 100) -> list[str]:
    if harness == "codex":
        return ["codex", "--no-daemon", "exec", "--ignore-user-config", "--ephemeral", "--json",
                "--skip-git-repo-check", "-m", model, "-s", "workspace-write",
                "-c", f"model_reasoning_effort={effort}", "-c", "mcp_servers={}",
                "-c", "features.multi_agent=false", "-c", "features.apps=false",
                "-c", "features.plugins=false", "-"]
    return ["claude", "-p", "--output-format", "stream-json", "--verbose",
            "--no-session-persistence", "--setting-sources", "user",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--tools", "Bash,Read,Write,Edit,Glob,Grep", "--model", model,
            "--effort", effort, "--permission-mode", "auto", "--permission-prompts", "none",
            "--max-turns", str(max_turns)]


def summarize(log: Path, harness: str) -> dict:
    answer, completed, failed = "", False, False
    usage, models = None, set()
    with log.open() as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if event.get("model"):
                models.add(event["model"])
            if harness == "codex":
                item = event.get("item") or {}
                if kind == "item.completed" and item.get("type") == "agent_message":
                    answer = item.get("text", "")
                if kind == "turn.completed":
                    completed, usage = True, event.get("usage")
                failed |= kind in {"error", "turn.failed"}
            elif kind == "result":
                completed = not event.get("is_error", False)
                failed |= not completed
                answer, usage = event.get("result", ""), event.get("usage")
                models.update((event.get("modelUsage") or {}).keys())
    return {"response": answer, "completed": completed and not failed and bool(answer.strip()),
            "usage": usage, "observed_models": sorted(models)}


def terminate(child: subprocess.Popen) -> None:
    # Children start their own group, so timeout/cancel also stops shell commands.
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        child.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait()


def run(args: argparse.Namespace) -> dict:
    root, task = args.project, args.task
    folder, path = paths(root, task)
    if not root.is_dir():
        raise ValueError("Project directory does not exist")
    for name in ("timeout", "budget", "max_attempts", "max_turns"):
        value = getattr(args, name)
        if value is not None and value <= 0:
            raise ValueError(f"{name} must be positive")
    (root / ".orchestrate").mkdir(mode=0o700, exist_ok=True)
    # Reconcile a killed supervisor before trying to resume it.
    if path.exists():
        read_state(root, task)
    with project_lock(root) as lock:
        state = json.loads(path.read_text()) if path.exists() else None
        if state and not args.retry:
            return state  # Idempotent, including failures. Never silently rerun work.
        if state and state["status"] == "running":
            raise ValueError("Task is still running")
        if state:
            for name in ("timeout", "budget", "max_attempts", "max_turns"):
                if getattr(args, name) not in (None, state.get(name, 100 if name == "max_turns" else None)):
                    raise ValueError("Retry cannot reset task budgets; keep the same task ID and limits")
        else:
            if args.retry:
                raise ValueError("Cannot retry a task that does not exist")
            if not args.harness or not args.prompt_file:
                raise ValueError("New tasks require --harness and --prompt-file")
            if args.max_turns is not None and args.harness != "claude":
                raise ValueError("--max-turns applies to Claude tasks only")
            folder.mkdir(mode=0o700, exist_ok=True)
            state = {"task": task, "project": str(root), "status": "pending", "attempts": [],
                     "timeout": args.timeout or 1200, "budget": args.budget or 3600,
                     "max_attempts": args.max_attempts or 3, "max_turns": args.max_turns or 100}
        if len(state["attempts"]) >= state["max_attempts"]:
            raise ValueError("Attempt budget exhausted; inspect results instead of starting another recovery cycle")
        remaining = state["budget"] - sum(a["seconds"] for a in state["attempts"])
        if remaining <= 0:
            raise ValueError("Time budget exhausted")
        previous = state["attempts"][-1] if state["attempts"] else {}
        harness = args.harness or previous["harness"]
        model = args.model or (previous.get("model") if harness == previous.get("harness") else None) or os.environ.get(MODEL_ENV[harness]) or DEFAULT_MODELS[harness]
        effort = args.effort or previous.get("effort", "medium")
        prompt = args.prompt_file.read_text() if args.prompt_file else Path(previous["prompt"]).read_text()
        if not prompt.strip():
            raise ValueError("Prompt file is empty")
        number = len(state["attempts"]) + 1
        prefix = folder / str(number)
        prompt_path, log, stderr, response = [prefix.with_suffix(s) for s in (".prompt.md", ".jsonl", ".stderr.log", ".response.md")]
        prompt_path.write_text(prompt)
        prompt_path.chmod(0o600)
        instruction = ("Complete only the task below in the current project. Do not spawn other agents, "
                       "run another orchestrator, commit, push, deploy, or edit this task's orchestration state. "
                       "Use shell/agent_tools, not MCP. Use playwright-cli for interactive browser work. "
                       "Keep useful existing work. Finish with changes, checks, and anything still blocked.\n")
        if previous:
            instruction += f"This is attempt {number} of the SAME task. Inspect existing work and previous evidence at {folder}. Continue from it.\n"
        attempt = {"number": number, "harness": harness, "model": model, "effort": effort,
                   "status": "running", "started_at": time.time(), "seconds": 0,
                   "prompt": str(prompt_path), "log": str(log), "stderr": str(stderr),
                   "response": str(response), "usage": None, "exit_code": None, "error": None}
        state["attempts"].append(attempt)
        state["status"] = "running"
        save(path, state)
        started, child = time.monotonic(), None
        old_handler = signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        try:
            env = child_env()
            subscription(harness, env, min(state["timeout"], remaining), root)
            # Use a file as stdin: large prompts cannot block the supervising loop.
            input_path = prefix.with_suffix(".input.md")
            input_path.write_text(instruction + "\n" + prompt)
            input_path.chmod(0o600)
            with input_path.open() as source, log.open("w") as output, stderr.open("w") as errors:
                child = subprocess.Popen(command(harness, model, effort, state.get("max_turns", 100)), cwd=root, env=env,
                                         stdin=source, stdout=output, stderr=errors,
                                         start_new_session=True, pass_fds=(lock.fileno(),))
                deadline = started + min(state["timeout"], remaining)
                while child.poll() is None:
                    cancel = folder / "cancel.json"
                    if cancel.exists() and json.loads(cancel.read_text()).get("attempt") == number:
                        attempt["status"] = "cancelled"
                        break
                    if time.monotonic() >= deadline:
                        attempt["status"] = "timed_out"
                        break
                    time.sleep(0.1)
                if attempt["status"] != "running":
                    terminate(child)
                attempt["exit_code"] = child.wait()
            result = summarize(log, harness)
            response.write_text(result["response"])
            attempt.update(usage=result["usage"], observed_models=result["observed_models"])
            if attempt["status"] == "running":
                attempt["status"] = "completed" if child.returncode == 0 and result["completed"] else "failed"
                if attempt["status"] == "failed":
                    attempt["error"] = "CLI failed or no successful final response; inspect log and stderr"
        except KeyboardInterrupt:
            attempt.update(status="cancelled", error="Interrupted by signal")
        except subprocess.TimeoutExpired:
            attempt.update(status="timed_out", error="Subscription check exceeded the attempt time limit")
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            attempt.update(status="failed", error=str(exc))
        finally:
            if child is not None and child.poll() is None:
                terminate(child)
            signal.signal(signal.SIGTERM, old_handler)
            attempt.update(seconds=round(time.monotonic() - started, 3), ended_at=time.time())
            state["status"] = attempt["status"]
            save(path, state)
        return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=resolve, required=True, help="Working directory; one active task per directory")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "status", "wait", "cancel"):
        sub = commands.add_parser(name)
        sub.add_argument("task")
        if name == "run":
            sub.add_argument("--harness", choices=list(DEFAULT_MODELS))
            sub.add_argument("--model")
            sub.add_argument("--effort", choices=["low", "medium", "high"])
            sub.add_argument("--prompt-file", type=resolve)
            sub.add_argument("--retry", action="store_true")
            sub.add_argument("--timeout", type=int, help="Seconds per attempt (default 1200)")
            sub.add_argument("--budget", type=int, help="Total seconds across attempts (default 3600)")
            sub.add_argument("--max-attempts", type=int, help="Total attempts (default 3)")
            sub.add_argument("--max-turns", type=int, help="Claude turns per attempt (default 100)")
        if name == "wait":
            sub.add_argument("--seconds", type=int, default=30, choices=range(1, 61), metavar="1..60")
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            state = run(args)
        else:
            state = read_state(args.project, args.task)
            if args.command == "cancel" and state["status"] == "running":
                folder, _ = paths(args.project, args.task)
                save(folder / "cancel.json", {"attempt": state["attempts"][-1]["number"]})
                state["cancel_requested"] = True
            if args.command == "wait":
                deadline = time.monotonic() + args.seconds
                while state["status"] == "running" and time.monotonic() < deadline:
                    time.sleep(0.2)
                    state = read_state(args.project, args.task)
        print(json.dumps(state, indent=2))
        return int(args.command == "run" and state["status"] != "completed")
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
