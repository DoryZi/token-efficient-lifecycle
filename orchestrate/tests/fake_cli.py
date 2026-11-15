"""Local process fixture. Never contacts a model service."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

args = sys.argv[1:]
assert "OPENAI_API_KEY" not in os.environ
assert "ANTHROPIC_API_KEY" not in os.environ
assert "CLAUDECODE" not in os.environ
root = Path.cwd()
if args[:2] == ["login", "status"]:
    print("Logged in using API key" if (root / "api-auth").exists() else "Logged in using ChatGPT")
    raise SystemExit()
if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "api_key" if (root / "api-auth").exists() else "claude.ai"}))
    raise SystemExit()
prompt = sys.stdin.read()
with (root / "calls.jsonl").open("a") as stream:
    stream.write(json.dumps({"args": args, "prompt": prompt, "cwd": str(root)}) + "\n")
if "SLEEP" in prompt:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    (root / "child.pid").write_text(str(child.pid))
    time.sleep(20)
if "FAIL" in prompt:
    print(json.dumps({"type": "error", "message": "simulated failure"}))
    raise SystemExit(1)
if "EMPTY" in prompt:
    raise SystemExit()
(root / "built.txt").write_text("completed useful work")
if Path(sys.argv[0]).name == "claude":
    print(json.dumps({"type": "result", "result": "Built and checked.", "is_error": False,
                      "modelUsage": {"fake-claude": {}}, "usage": {"input_tokens": 10, "output_tokens": 2}}))
else:
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Built and checked."}}))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 3, "output_tokens": 2}}))
