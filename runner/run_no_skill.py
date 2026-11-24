from evaluation_private.evaluator import evaluate, ensure_tests_empty
import argparse
import json
import uuid
from pathlib import Path

import yaml

from agents.claude_agent import ClaudeAgent
from docker.environment import DockerEnvironment

ROOT = Path(__file__).resolve().parents[1]

parser = argparse.ArgumentParser()
parser.add_argument("--task", required=True)
parser.add_argument("--batch", default="batch1")
parser.add_argument("--budget", type=float, default=0.5)
args = parser.parse_args()

tasks = json.loads(
    (ROOT / "metadata" / f"{args.batch}.json").read_text()
)

task = next(
    (t for t in tasks if t["task_id"] == args.task),
    None
)

if task is None:
    raise SystemExit("Task not found")

models = yaml.safe_load(
    (ROOT / "configs/models.yaml").read_text()
)

model = models["models"]["target_agent"]["model_id"]

prompt = (ROOT / task["task_file"]).read_text()

run_id = f"pilot_{uuid.uuid4().hex[:8]}"

output_dir = (
    ROOT / "trajectories/no_skill"
    / args.batch
    / args.task
    / run_id
)

output_dir.mkdir(parents=True, exist_ok=False)

(output_dir / "task.md").write_text(prompt)

(output_dir / "metadata.json").write_text(
    json.dumps(task, indent=2)
)

agent = ClaudeAgent(model)

print("Task:", task["instance_id"], flush=True)
print("Model:", model, flush=True)
print("Condition: No Skill", flush=True)
print("Run ID:", run_id, flush=True)
print("Output:", output_dir, flush=True)

with DockerEnvironment(task) as environment:
    ensure_tests_empty(environment, task)

    print("Container:", environment.name, flush=True)
    print("Repository:", environment.repo_path, flush=True)

    environment_info = {
        "image": environment.image,
        "image_id": environment.image_id,
        "platform": environment.platform,
        "repo_path": environment.repo_path
    }

    (output_dir / "environment.json").write_text(
        json.dumps(environment_info, indent=2)
    )

    print("Agent started...", flush=True)

    metrics = agent.run(
        environment=environment,
        prompt=prompt,
        output_dir=output_dir,
        timeout=task.get("timeout_sec") or 1800,
        budget=args.budget
    )

    if not metrics["timed_out"]:
        patch = environment.execute(
            "git add -N -- . && git diff --binary HEAD"
        )

        (output_dir / "changes.patch").write_text(
            patch["stdout"]
        )

        (output_dir / "patch_status.json").write_text(
            json.dumps({
                "exit_code": patch["exit_code"],
                "stderr": patch["stderr"]
            }, indent=2)
        )

        status = environment.execute("git status --short")

        (output_dir / "git_status.txt").write_text(
            status["stdout"]
        )

    evaluate(environment, task, output_dir, metrics)

print(json.dumps(metrics, indent=2))
print("Container removed.")
print("Results saved to:", output_dir)
