from evaluation_private.evaluator import evaluate, ensure_tests_empty
import argparse
import hashlib
import json
import uuid
from pathlib import Path

import yaml

from docker.environment import DockerEnvironment
from agents.claude_agent import ClaudeAgent

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(path.read_text())


def save_json(path, data):
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("--batch", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--budget", type=float, default=0.5)

    args = parser.parse_args()

    relative = (
        Path(args.batch)
        / args.task
        / args.source_run
    )

    source_dir = (
        ROOT / "trajectories/no_skill" / relative
    )

    skill_dir = (
        ROOT / "rgkp/residual_skills" / relative
    )

    manifest = read_json(
        skill_dir / "manifest.json"
    )

    if manifest["status"] == "empty_residual":
        raise SystemExit(
            "Empty residual. No skill available for this pilot."
        )

    if manifest["status"] != "generated":
        raise ValueError("Invalid skill manifest")

    task = read_json(
        source_dir / "metadata.json"
    )

    task_text = (
        source_dir / "task.md"
    ).read_text()

    skill = (
        skill_dir / "SKILL.md"
    ).read_text()

    if not skill.strip():
        raise ValueError("Empty skill")

    models = yaml.safe_load(
        (ROOT / "configs/models.yaml").read_text()
    )

    model = models["models"]["target_agent"]["model_id"]

    source_metrics = read_json(
        source_dir / "metrics.json"
    )

    if source_metrics["requested_model"] != model:
        raise ValueError("Model mismatch with source run")

    run_id = f"pilot_{uuid.uuid4().hex[:8]}"

    output_dir = (
        ROOT / "executions/with_skill/rgkp"
        / args.batch / args.task / run_id
    )

    output_dir.mkdir(parents=True, exist_ok=False)

    # Preserve the original task description.
    # Append only the generated residual skill.
    prompt = (
        task_text.rstrip()
        + "\n\n"
        + "Additional skill guidance for this task:\n\n"
        + skill
    )

    (output_dir / "task.md").write_text(task_text)
    (output_dir / "SKILL.md").write_text(skill)
    (output_dir / "prompt.txt").write_text(prompt)

    save_json(output_dir / "metadata.json", task)

    timeout = int(task.get("timeout_sec") or 1800)

    save_json(
        output_dir / "execution_config.json",
        {
            "condition": "rgkp_skill",
            "instance_id": task["instance_id"],
            "run_id": run_id,
            "source_no_skill_run": args.source_run,
            "model": model,
            "timeout_sec": timeout,
            "budget_usd": args.budget,
            "skill_delivery": "inline_prompt",
            "skill_sha256": hashlib.sha256(
                skill.encode("utf-8")
            ).hexdigest(),
            "skill_characters": len(skill)
        }
    )

    print("Task:", task["instance_id"], flush=True)
    print("Model:", model, flush=True)
    print("Condition: RGKP Skill", flush=True)
    print("Source run:", args.source_run, flush=True)
    print("New run:", run_id, flush=True)
    print("Skill characters:", len(skill), flush=True)
    print("Starting fresh Docker container...", flush=True)

    try:

        with DockerEnvironment(task) as environment:
            ensure_tests_empty(environment, task)

            save_json(
                output_dir / "environment.json",
                {
                    "container": environment.name,
                    "image_id": environment.image_id,
                    "platform": environment.platform,
                    "repository_path": environment.repo_path
                }
            )

            before = environment.execute(
                "git rev-parse HEAD && git status --short"
            )

            save_json(
                output_dir / "initial_repository.json",
                before
            )

            if before["exit_code"] != 0:
                raise RuntimeError("Repository check failed")

            if task["repo_source"] == "git":

                lines = before["stdout"].splitlines()

                if lines != [task["repo_commit"]]:
                    raise RuntimeError(
                        "Initial repository is not clean "
                        "or commit does not match"
                    )

            agent = ClaudeAgent(model)

            print("Running agent with skill...", flush=True)

            metrics = agent.run(
                environment,
                prompt,
                output_dir,
                timeout=timeout,
                budget=args.budget
            )

            if not isinstance(metrics, dict):
                raise TypeError(
                    "Unexpected ClaudeAgent.run return type"
                )

            save_json(
                output_dir / "metrics.json",
                metrics
            )

            status = environment.execute(
                "git status --short"
            )

            (output_dir / "git_status.txt").write_text(
                status["stdout"]
            )

            if not metrics.get("timed_out", False):

                patch = environment.execute(
                    "git add -N -- . && git diff --binary HEAD"
                )

                (output_dir / "changes.patch").write_text(
                    patch["stdout"]
                )

                save_json(
                    output_dir / "patch_status.json",
                    {
                        "exit_code": patch["exit_code"],
                        "stderr": patch["stderr"]
                    }
                )

            evaluate(environment, task, output_dir, metrics)

    except Exception as error:

        save_json(
            output_dir / "execution_error.json",
            {
                "type": type(error).__name__,
                "message": str(error)
            }
        )

        raise

    print("\n===== With Skill Execution Summary =====")

    print(json.dumps(
        metrics,
        indent=2,
        ensure_ascii=False
    ))

    print("\nResults saved to:", output_dir)


if __name__ == "__main__":
    main()
