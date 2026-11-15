import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import orchestrate

TOOL = Path(orchestrate.__file__)


class OrchestrateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "project with spaces"
        self.root.mkdir()
        self.bin = base / "bin"
        self.bin.mkdir()
        fixture = Path(__file__).with_name("fake_cli.py").read_text()
        for harness in ("codex", "claude"):
            cli = self.bin / harness
            cli.write_text(f"#!{sys.executable}\n" + fixture)
            cli.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        OPENAI_API_KEY="must-not-pass", ANTHROPIC_API_KEY="must-not-pass", CLAUDECODE="nested")
        self.prompt = base / "task.md"
        self.prompt.write_text("Build one small feature.")

    def cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, str(TOOL), "--project", str(self.root), *args],
                                env=self.env, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def run_task(self, *args, harness="codex", expected=0):
        return self.cli("run", "fix", "--harness", harness, "--prompt-file", str(self.prompt),
                        *args, expected=expected)

    def background(self):
        child = subprocess.Popen([sys.executable, str(TOOL), "--project", str(self.root), "run", "fix",
                                  "--harness", "codex", "--prompt-file", str(self.prompt), "--timeout", "5"],
                                 env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: child.poll() is None and child.kill())
        deadline = time.monotonic() + 4
        while not (self.root / "child.pid").exists():
            if child.poll() is not None or time.monotonic() > deadline:
                self.fail("Worker did not start: " + str(child.communicate()))
            time.sleep(0.03)
        return child

    def assert_child_stopped(self):
        pid = int((self.root / "child.pid").read_text())
        stat = Path(f"/proc/{pid}/stat")
        deadline = time.monotonic() + 2
        while stat.exists() and stat.read_text().split()[2] != "Z" and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(not stat.exists() or stat.read_text().split()[2] == "Z")

    def test_default_model_comes_from_the_environment_and_the_flag_still_wins(self):
        self.env["ORCHESTRATE_CLAUDE_MODEL"] = "haiku"
        result = self.cli("run", "cheap", "--harness", "claude", "--prompt-file", str(self.prompt))
        self.assertEqual(result["attempts"][0]["model"], "haiku")
        result = self.cli("run", "flag", "--harness", "claude", "--model", "opus", "--prompt-file", str(self.prompt))
        self.assertEqual(result["attempts"][0]["model"], "opus")

    def test_both_harnesses_execute_in_project_and_record_evidence(self):
        for harness in ("codex", "claude"):
            with self.subTest(harness=harness):
                result = self.cli("run", harness, "--harness", harness, "--prompt-file", str(self.prompt))
                self.assertEqual(result["status"], "completed")
                attempt = result["attempts"][0]
                self.assertEqual(attempt["model"], orchestrate.DEFAULT_MODELS[harness])
                self.assertEqual(attempt["usage"]["output_tokens"], 2)
                self.assertEqual(Path(attempt["response"]).read_text(), "Built and checked.")
                self.assertEqual((self.root / "built.txt").read_text(), "completed useful work")
        calls = [json.loads(line) for line in (self.root / "calls.jsonl").read_text().splitlines()]
        self.assertIn("mcp_servers={}", calls[0]["args"])
        self.assertIn("--strict-mcp-config", calls[1]["args"])
        self.assertIn('{"mcpServers":{}}', calls[1]["args"])

    def test_rerun_is_idempotent_and_retry_preserves_limits_and_work(self):
        first = self.run_task("--max-attempts", "2")
        self.assertEqual(self.run_task(), first)
        self.prompt.write_text("Continue existing work, fix the remaining assertion.")
        second = self.run_task("--retry", "--model", "gpt-6-astra")
        self.assertEqual(len(second["attempts"]), 2)
        self.assertEqual(second["attempts"][1]["model"], "gpt-6-astra")
        self.assertTrue(Path(second["attempts"][0]["response"]).exists())
        self.assertIn("Attempt budget exhausted", self.run_task("--retry", expected=2)["error"])
        self.assertEqual(len((self.root / "calls.jsonl").read_text().splitlines()), 2)

    def test_failed_task_does_not_retry_automatically(self):
        self.prompt.write_text("FAIL")
        state = self.run_task(expected=1)
        self.assertEqual(state["status"], "failed")
        self.assertEqual(self.run_task(expected=1), state)
        self.assertEqual(len((self.root / "calls.jsonl").read_text().splitlines()), 1)
        self.assertIn("cannot reset", self.run_task("--retry", "--budget", "9999", expected=2)["error"])

    def test_successful_exit_without_response_is_failure(self):
        self.prompt.write_text("EMPTY")
        self.assertEqual(self.run_task(expected=1)["status"], "failed")

    def test_auth_rejects_paid_api_without_starting_work(self):
        (self.root / "api-auth").touch()
        for harness in ("claude", "codex"):
            result = self.cli("run", harness, "--harness", harness, "--prompt-file", str(self.prompt), expected=1)
            self.assertIn("subscription login required", result["attempts"][-1]["error"])
        self.assertFalse((self.root / "calls.jsonl").exists())

    def test_timeout_stops_process_tree_and_persists_budget(self):
        self.prompt.write_text("SLEEP")
        result = self.run_task("--timeout", "1", "--budget", "1", expected=1)
        self.assertEqual(result["status"], "timed_out")
        self.assert_child_stopped()
        self.assertIn("Time budget exhausted", self.run_task("--retry", expected=2)["error"])

    def test_cancel_wait_and_one_active_task(self):
        self.prompt.write_text("SLEEP")
        worker = self.background()
        self.assertEqual(self.cli("wait", "fix", "--seconds", "1")["status"], "running")
        self.assertIn("already running", self.cli("run", "other", "--harness", "codex",
                                                  "--prompt-file", str(self.prompt), expected=2)["error"])
        self.assertTrue(self.cli("cancel", "fix")["cancel_requested"])
        output, errors = worker.communicate(timeout=5)
        self.assertEqual(worker.returncode, 1, errors)
        self.assertEqual(json.loads(output)["status"], "cancelled")
        self.assertEqual(self.cli("wait", "fix", "--seconds", "1")["status"], "cancelled")
        self.assert_child_stopped()
        self.prompt.write_text("Resume useful work.")
        self.assertEqual(self.run_task("--retry")["status"], "completed")

    def test_sigterm_cleans_up_child_and_saves_state(self):
        self.prompt.write_text("SLEEP")
        worker = self.background()
        worker.send_signal(signal.SIGTERM)
        output, errors = worker.communicate(timeout=5)
        self.assertEqual(json.loads(output)["status"], "cancelled", errors)
        self.assert_child_stopped()

    def test_abandoned_checkpoint_is_reconciled_without_losing_attempts(self):
        state = self.run_task()
        state["status"] = state["attempts"][0]["status"] = "running"
        folder, path = orchestrate.paths(self.root, "fix")
        orchestrate.save(path, state)
        recovered = self.cli("status", "fix")
        self.assertEqual(recovered["status"], "interrupted")
        self.assertEqual(len(recovered["attempts"]), 1)
        self.assertTrue(Path(recovered["attempts"][0]["response"]).exists())

    def test_invalid_input_does_not_launch(self):
        self.cli("run", "../escape", "--harness", "codex", "--prompt-file", str(self.prompt), expected=2)
        self.run_task("--timeout", "0", expected=2)
        self.prompt.write_text("")
        self.run_task(expected=2)
        self.assertFalse((self.root / "calls.jsonl").exists())
        self.prompt.write_text("Now valid")
        self.assertEqual(self.run_task()["status"], "completed")

    def turns(self, index=-1):
        calls = [json.loads(line) for line in (self.root / "calls.jsonl").read_text().splitlines()]
        args = calls[index]["args"]
        return args[args.index("--max-turns") + 1]

    def test_max_turns_default_choice_retry_and_refusals(self):
        self.run_task("--max-attempts", "5", harness="claude")
        self.assertEqual(self.turns(), "100")
        self.run_task("--retry", harness="claude")
        self.assertEqual(self.turns(), "100")
        self.cli("run", "two", "--harness", "claude", "--prompt-file", str(self.prompt), "--max-turns", "7")
        self.assertEqual(self.turns(), "7")
        self.cli("run", "two", "--harness", "claude", "--retry", "--max-turns", "7")
        self.assertEqual(self.turns(), "7")
        self.assertIn("cannot reset", self.cli("run", "two", "--retry", "--max-turns", "8", expected=2)["error"])
        self.assertIn("positive", self.cli("run", "three", "--harness", "claude", "--prompt-file",
                                           str(self.prompt), "--max-turns", "0", expected=2)["error"])
        self.assertIn("Claude", self.cli("run", "four", "--harness", "codex", "--prompt-file",
                                         str(self.prompt), "--max-turns", "5", expected=2)["error"])

    def test_older_state_without_max_turns_uses_100(self):
        state = self.run_task(harness="claude")
        del state["max_turns"]
        orchestrate.save(orchestrate.paths(self.root, "fix")[1], state)
        self.run_task("--retry", harness="claude")
        self.assertEqual(self.turns(), "100")

    def test_relative_paths_resolve_from_invoking_shell(self):
        with patch.dict(os.environ, {"PWD": str(self.root)}):
            self.assertEqual(orchestrate.resolve("task.md"), self.root / "task.md")


if __name__ == "__main__":
    unittest.main()
