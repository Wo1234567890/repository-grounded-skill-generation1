import os
import json
import subprocess
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


class ClaudeAgent:

    def __init__(self, model):
        load_dotenv(ROOT / ".env")

        self.model = model

        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("Missing ANTHROPIC_API_KEY")

    def run(self, environment, prompt, output_dir,
            timeout=1800, budget=0.5):

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        trajectory_path = output_dir / "trajectory.jsonl"
        stderr_path = output_dir / "stderr.log"

        command = [
            "docker", "exec", "-i",
            "-e", "ANTHROPIC_API_KEY",
            "-w", environment.repo_path,
            environment.name,
            "claude",
            "--print",
            "--model", self.model,
            "--output-format", "stream-json",
            "--verbose",
            "--no-session-persistence",
            "--disable-slash-commands",
            "--setting-sources", "",
            "--strict-mcp-config",
            "--mcp-config", '{"mcpServers":{}}',
            "--permission-mode", "dontAsk",
            "--tools", "Read,Edit,Write,Glob,Grep,Bash",
            "--allowedTools",
            "Read,Edit,Write,Glob,Grep,"
            "Bash(git status*),Bash(git diff*),"
            "Bash(chmod +x *),Bash(bash -n *),"
            "Bash(mkdir -p *),Bash(kustomize build *)",
            "--max-budget-usd", str(budget)
        ]

        start = time.monotonic()
        timed_out = False
        exit_code = None

        with trajectory_path.open("wb") as stdout, \
                stderr_path.open("wb") as stderr:

            try:
                process = subprocess.run(
                    command,
                    input=prompt.encode("utf-8"),
                    stdout=stdout,
                    stderr=stderr,
                    timeout=timeout,
                    env=os.environ.copy()
                )

                exit_code = process.returncode

            except subprocess.TimeoutExpired:
                timed_out = True

        wall_time = time.monotonic() - start

        result = {}
        tool_calls = set()
        actual_model = None
        claude_version = None

        with trajectory_path.open(
            encoding="utf-8", errors="replace"
        ) as f:

            for line in f:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if event.get("type") == "system":
                    if event.get("subtype") == "init":
                        actual_model = event.get("model")
                        claude_version = event.get(
                            "claude_code_version"
                        )

                if event.get("type") == "result":
                    result = event

                if event.get("type") == "assistant":
                    message = event.get("message", {})

                    for content in message.get("content", []):
                        if content.get("type") == "tool_use":
                            if content.get("id"):
                                tool_calls.add(content["id"])

        usage = result.get("usage", {})

        metrics = {
            "requested_model": self.model,
            "actual_model": actual_model,
            "claude_code_version": claude_version,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "wall_time_sec": wall_time,
            "duration_ms": result.get("duration_ms"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_creation_tokens": usage.get(
                "cache_creation_input_tokens"
            ),
            "cache_read_tokens": usage.get(
                "cache_read_input_tokens"
            ),
            "cost_usd": result.get("total_cost_usd"),
            "num_turns": result.get("num_turns"),
            "tool_calls": len(tool_calls),
            "is_error": result.get("is_error"),
            "terminal_reason": result.get("terminal_reason"),
            "permission_denials": result.get(
                "permission_denials"
            ),
            "execution_completed": (
                exit_code == 0
                and not timed_out
                and result.get("subtype") == "success"
                and result.get("is_error") is False
                and actual_model == self.model
            )
        }

        (output_dir / "metrics.json").write_text(
            json.dumps(metrics, indent=2),
            encoding="utf-8"
        )

        (output_dir / "result.json").write_text(
            json.dumps(result, indent=2),
            encoding="utf-8"
        )

        return metrics
