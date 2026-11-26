import argparse
import json
import sys
import subprocess
import fcntl

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "results/pilot10"
OUTPUT.mkdir(parents=True, exist_ok=True)


def load(path):
    return json.loads(path.read_text())


def save(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False)
    )
    tmp.replace(path)


def run(module, arguments, log):
    command = [
        sys.executable, "-m", module, *arguments
    ]

    with log.open("a") as f:
        f.write("\nCOMMAND: " + " ".join(command) + "\n")
        f.flush()

        result = subprocess.run(
            command,
            cwd=ROOT,
            stdout=f,
            stderr=subprocess.STDOUT
        )

    if result.returncode != 0:
        raise RuntimeError(
            f"{module} failed. See {log}"
        )


def directories(path):
    if not path.exists():
        return set()

    return {
        p.name for p in path.iterdir()
        if p.is_dir()
    }


def read_execution(path):
    metrics = load(path / "metrics.json")
    evaluation = load(path / "evaluation.json")

    if not metrics.get("execution_completed"):
        raise RuntimeError(f"Incomplete execution: {path}")

    if evaluation["status"] not in ("pass", "fail"):
        raise RuntimeError(
            f"Invalid evaluation: {path}"
        )

    return {
        "run_id": path.name,
        "evaluation": evaluation,
        "metrics": metrics
    }


def process(task, budget):
    batch = task["batch"]
    task_id = task["task_id"]

    folder = OUTPUT / batch / task_id
    folder.mkdir(parents=True, exist_ok=True)

    state_file = folder / "state.json"
    log = folder / "pipeline.log"

    source_root = (
        ROOT / "trajectories/no_skill"
        / batch / task_id
    )

    with_root = (
        ROOT / "executions/with_skill/rgkp"
        / batch / task_id
    )

    print(f"[START] {task_id}", flush=True)

    result = {
        "task_id": task_id,
        "batch": batch,
        "status": "error"
    }

    try:
        if state_file.exists():
            state = load(state_file)

            if state["budget"] != budget:
                raise RuntimeError("Budget mismatch")

            if state.get("status") == "completed":
                print(f"[SKIP] {task_id}", flush=True)
                return state["result"]

        else:
            state = {
                "budget": budget,
                "previous_runs": sorted(
                    directories(source_root)
                )
            }
            save(state_file, state)

        arguments = [
            "--batch", batch,
            "--task", task_id,
            "--budget", str(budget)
        ]

        source = state.get("source_run")

        if not source:
            previous = set(state["previous_runs"])

            new = directories(source_root) - previous

            if not new:
                try:
                    run(
                        "runner.run_no_skill",
                        arguments,
                        log
                    )
                finally:
                    new = directories(source_root) - previous

                    if len(new) == 1:
                        state["source_run"] = next(iter(new))
                        save(state_file, state)

            if len(new) != 1:
                raise RuntimeError(
                    f"Ambiguous source runs: {sorted(new)}"
                )

            source = next(iter(new))
            state["source_run"] = source
            save(state_file, state)

        result["no_skill"] = read_execution(
            source_root / source
        )

        print(
            f"[NO SKILL] {task_id}: "
            f"{result['no_skill']['evaluation']['status']}",
            flush=True
        )

        run(
            "runner.run_pipeline",
            arguments + ["--source-run", source],
            log
        )

        pipeline_path = (
            ROOT / "logs/pipeline"
            / batch / task_id / source
            / "pipeline.json"
        )

        pipeline = load(pipeline_path)
        result["pipeline"] = pipeline

        if pipeline["status"] == "empty_residual":
            raise RuntimeError("Non-empty skill required")

        elif pipeline["status"] == "completed":
            matches = []

            for path in with_root.glob("*/execution_config.json"):
                config = load(path)

                if config.get("source_no_skill_run") == source:
                    matches.append(path.parent)

            if len(matches) != 1:
                raise RuntimeError(
                    f"Expected one With Skill run, got {len(matches)}"
                )

            result["with_skill"] = read_execution(
                matches[0]
            )

            result["status"] = "completed"

        else:
            raise RuntimeError(
                f"Pipeline status: {pipeline['status']}"
            )

        state["status"] = result["status"]
        state["result"] = result
        save(state_file, state)

    except Exception as error:
        result["error"] = str(error)

    save(folder / "result.json", result)

    print(
        f"[{result['status'].upper()}] {task_id}",
        result.get("error", ""),
        flush=True
    )

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--budget", type=float, default=0.5)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    tasks = load(ROOT / "metadata/batch1.json")

    tasks = [
        t for t in tasks
        if t["repo_source"] == "git"
    ][:10]

    print("===== Pilot Tasks =====")

    for i, task in enumerate(tasks, 1):
        print(i, task["task_id"])

    if not args.execute:
        print("\nDry run only. No API calls.")
        return

    with (OUTPUT / "batch.lock").open("w") as lock:
        fcntl.flock(
            lock, fcntl.LOCK_EX | fcntl.LOCK_NB
        )

        with ThreadPoolExecutor(
            max_workers=args.workers
        ) as pool:
            results = list(
                pool.map(
                    lambda t: process(t, args.budget),
                    tasks
                )
            )

        save(OUTPUT / "summary.json", results)

        print("\n===== Pilot Summary =====")

        for result in results:
            no_skill = result.get("no_skill") or {}
            with_skill = result.get("with_skill") or {}

            print(
                result["task_id"],
                "No Skill:",
                no_skill.get("evaluation", {}).get("status", "-"),
                "With Skill:",
                with_skill.get("evaluation", {}).get("status", "-"),
                "Pipeline:",
                result["status"]
            )

        for condition in ["no_skill", "with_skill"]:
            runs = [
                r[condition] for r in results
                if r.get(condition)
            ]

            passed = sum(
                r["evaluation"]["passed"] is True
                for r in runs
            )

            print(f"\n===== {condition} =====")
            print("Evaluated:", len(runs))
            print("Passed:", passed)
            print(
                "Pass rate:",
                passed / len(runs) if runs else None
            )

            for key in [
                "input_tokens",
                "output_tokens",
                "cache_creation_tokens",
                "cache_read_tokens",
                "wall_time_sec",
                "cost_usd",
                "tool_calls"
            ]:
                total = sum(
                    r["metrics"].get(key) or 0
                    for r in runs
                )
                print(key, ":", total)

        print("\nResults:", OUTPUT / "summary.json")


if __name__ == "__main__":
    main()
