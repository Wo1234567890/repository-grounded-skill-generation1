import argparse
import fcntl
import hashlib
import json
import subprocess
import sys
import time

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(path.read_text())


def save_json(path, data):

    path.parent.mkdir(parents=True, exist_ok=True)

    temp = path.with_suffix(".tmp")

    temp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    temp.replace(path)


def execute_module(module, arguments):

    command = [
        sys.executable,
        "-u",
        "-m",
        module,
        *arguments
    ]

    print("\nRunning:", " ".join(command), flush=True)

    subprocess.run(
        command,
        cwd=ROOT,
        check=True
    )


def create_trajectory(args):

    base = (
        ROOT / "trajectories/no_skill"
        / args.batch / args.task
    )

    base.mkdir(parents=True, exist_ok=True)

    before = set(base.iterdir())

    execute_module(
        "runner.run_no_skill",
        [
            "--batch", args.batch,
            "--task", args.task,
            "--budget", str(args.budget)
        ]
    )

    created = [
        path for path in set(base.iterdir()) - before
        if path.is_dir()
    ]

    if len(created) != 1:
        raise RuntimeError(
            "Cannot uniquely identify new trajectory"
        )

    return created[0].name


def run_stage(name, module, marker, arguments):

    if marker.exists():

        read_json(marker)

        print(
            f"[SKIP] {name}: existing result",
            flush=True
        )

        return {
            "stage": name,
            "status": "reused"
        }

    print(f"\n[START] {name}", flush=True)

    start = time.monotonic()

    execute_module(module, arguments)

    if not marker.exists():
        raise RuntimeError(
            f"Missing stage output: {marker}"
        )

    read_json(marker)

    elapsed = time.monotonic() - start

    print(f"[DONE] {name}: {elapsed:.2f}s", flush=True)

    return {
        "stage": name,
        "status": "completed",
        "wall_time_sec": elapsed
    }


def find_skill_runs(base, source_run, model, budget, skill_hash):

    if not base.exists():
        return []

    results = []

    for path in sorted(base.iterdir()):

        config_path = path / "execution_config.json"

        if not config_path.exists():
            continue

        config = read_json(config_path)

        if (
            config["source_no_skill_run"] == source_run
            and config["model"] == model
            and config["budget_usd"] == budget
            and config["skill_sha256"] == skill_hash
        ):
            results.append(path)

    return results


def run_pipeline(args):

    start = time.monotonic()

    source_run = args.source_run

    if not source_run:
        source_run = create_trajectory(args)

    relative = (
        Path(args.batch) / args.task / source_run
    )

    run_dir = (
        ROOT / "trajectories/no_skill" / relative
    )

    source_metrics = read_json(
        run_dir / "metrics.json"
    )

    if not source_metrics["execution_completed"]:
        raise RuntimeError("Source execution incomplete")

    models = yaml.safe_load(
        (ROOT / "configs/models.yaml").read_text()
    )

    model = models["models"]["target_agent"]["model_id"]

    if source_metrics["actual_model"] != model:
        raise ValueError("Source model mismatch")

    print("\n===== RGKP Pipeline =====", flush=True)
    print("Task:", args.task, flush=True)
    print("Source run:", source_run, flush=True)
    print("Model:", model, flush=True)

    arguments = [
        "--batch", args.batch,
        "--task", args.task,
        "--run", source_run
    ]

    stages = [
        (
            "Gap Discovery",
            "rgkp.gap_discovery",
            ROOT / "rgkp/gaps" / relative / "gaps.json"
        ),
        (
            "Repository Retrieval",
            "rgkp.repository_retrieval",
            ROOT / "rgkp/evidence" / relative / "evidence.json"
        ),
        (
            "Knowledge Targets",
            "rgkp.knowledge_target",
            ROOT / "rgkp/targets" / relative / "targets.json"
        ),
        (
            "Diagnostic Questions",
            "rgkp.diagnostic_question",
            ROOT / "rgkp/probes" / relative
            / "questions/questions.json"
        ),
        (
            "Baseline Probing",
            "rgkp.baseline_probing",
            ROOT / "rgkp/probes" / relative
            / "baseline/baseline.json"
        ),
        (
            "Repository Check",
            "rgkp.repository_check",
            ROOT / "rgkp/candidates" / relative
            / "routing/routing.json"
        ),
        (
            "Candidate Generation",
            "rgkp.candidate_generation",
            ROOT / "rgkp/candidates" / relative
            / "generation/candidates.json"
        ),
        (
            "Intervention",
            "rgkp.intervention",
            ROOT / "rgkp/interventions" / relative
            / "selections.json"
        ),
        (
            "Application Check",
            "rgkp.application_check",
            ROOT / "rgkp/residual_skills" / relative
            / "guidance/application_check.json"
        ),
        (
            "Residual Construction",
            "rgkp.residual_construction",
            ROOT / "rgkp/residual_skills" / relative
            / "manifest.json"
        )
    ]

    summary = {
        "batch": args.batch,
        "task": args.task,
        "source_run": source_run,
        "model": model,
        "status": "running",
        "stages": [],
        "with_skill_run": None
    }

    summary_path = (
        ROOT / "logs/pipeline"
        / relative / "pipeline.json"
    )

    save_json(summary_path, summary)

    try:

        for name, module, marker in stages:

            record = run_stage(
                name, module, marker, arguments
            )

            summary["stages"].append(record)

            save_json(summary_path, summary)

        skill_dir = (
            ROOT / "rgkp/residual_skills" / relative
        )

        manifest = read_json(
            skill_dir / "manifest.json"
        )

        summary["residual_status"] = manifest["status"]

        if manifest["status"] == "empty_residual":

            print("\nEmpty residual. Skipping pilot execution.")

            summary["status"] = "empty_residual"

        elif manifest["status"] == "generated":

            skill = (
                skill_dir / "SKILL.md"
            ).read_text()

            skill_hash = hashlib.sha256(
                skill.encode("utf-8")
            ).hexdigest()

            base = (
                ROOT / "executions/with_skill/rgkp"
                / args.batch / args.task
            )

            existing = find_skill_runs(
                base,
                source_run,
                model,
                args.budget,
                skill_hash
            )

            if len(existing) > 1:
                raise RuntimeError("Multiple matching runs")

            if existing:

                output = existing[0]

                print(
                    "\n[SKIP] Existing With Skill run:",
                    output.name,
                    flush=True
                )

            else:

                execute_module(
                    "runner.run_with_skill",
                    [
                        "--batch", args.batch,
                        "--task", args.task,
                        "--source-run", source_run,
                        "--budget", str(args.budget)
                    ]
                )

                matching = find_skill_runs(
                    base,
                    source_run,
                    model,
                    args.budget,
                    skill_hash
                )

                if len(matching) != 1:
                    raise RuntimeError(
                        "Cannot identify skill execution"
                    )

                output = matching[0]

            if not (output / "metrics.json").exists():
                raise RuntimeError(
                    f"Incomplete existing run: {output}"
                )

            metrics = read_json(
                output / "metrics.json"
            )

            if not metrics["execution_completed"]:
                raise RuntimeError("Skill execution incomplete")

            summary["with_skill_run"] = output.name
            summary["with_skill_metrics"] = metrics
            summary["status"] = "completed"

        else:

            raise ValueError("Invalid residual status")

    except Exception as error:

        summary["status"] = "failed"

        summary["error"] = {
            "type": type(error).__name__,
            "message": str(error)
        }

        raise

    finally:

        summary["pipeline_invocation_wall_time_sec"] = (
            time.monotonic() - start
        )

        save_json(summary_path, summary)

    print("\n===== Pipeline Summary =====")
    print("Task:", args.task)
    print("Source run:", source_run)
    print("With skill run:", summary["with_skill_run"])
    print("Status:", summary["status"])
    print("Summary:", summary_path)


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--batch",
        choices=["batch1", "batch2"],
        required=True
    )

    parser.add_argument("--task", required=True)
    parser.add_argument("--source-run")

    parser.add_argument(
        "--budget", type=float, default=0.5
    )

    args = parser.parse_args()

    lock_dir = ROOT / "logs/pipeline/locks"
    lock_dir.mkdir(parents=True, exist_ok=True)

    lock_path = (
        lock_dir / f"{args.batch}__{args.task}.lock"
    )

    with lock_path.open("a") as lock:

        try:

            fcntl.flock(
                lock,
                fcntl.LOCK_EX | fcntl.LOCK_NB
            )

        except BlockingIOError:

            raise SystemExit(
                "Pipeline already running for this task."
            )

        run_pipeline(args)


if __name__ == "__main__":
    main()
