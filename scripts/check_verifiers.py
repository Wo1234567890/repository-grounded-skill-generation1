import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from docker.environment import DockerEnvironment
from evaluation_private.evaluator import (
    get_config,
    inject_verifier,
    run_unit_test
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "results/verifier_sanity"
OUTPUT.mkdir(parents=True, exist_ok=True)

tasks = json.loads(
    (ROOT / "metadata/batch1.json").read_text()
)

tasks = [
    t for t in tasks
    if t["repo_source"] == "git"
][:10]


def check(task):
    task_id = task["task_id"]
    print(f"[START] {task_id}", flush=True)

    result = {"task_id": task_id}

    try:
        with DockerEnvironment(task) as env:
            result["repo_path"] = env.repo_path

            result["pytest_check"] = env.execute(
                "python -m pytest --version",
                timeout=60
            )

            if result["pytest_check"]["exit_code"] != 0:
                raise RuntimeError("pytest unavailable")

            result["verifier_sha256"] = inject_verifier(
                env, task
            )

            config = next(
                item for item in get_config(task)
                if item["method"] == "unit_test"
            )

            result.update(
                run_unit_test(
                    env, task, config["params"]
                )
            )

    except Exception as error:
        result["status"] = "evaluation_error"
        result["reason"] = str(error)

    (OUTPUT / f"{task_id}.json").write_text(
        json.dumps(result, indent=2)
    )

    print(
        f"[{result['status'].upper()}] {task_id}",
        f"passed={result.get('passed')}",
        f"failed={result.get('failed')}",
        f"reason={result.get('reason', '')}",
        flush=True
    )

    return result


with ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(check, tasks))

(OUTPUT / "summary.json").write_text(
    json.dumps(results, indent=2)
)

print("\n===== Verifier Sanity Check =====")

for r in results:
    print(
        r["task_id"],
        r["status"],
        "pass_rate:", r.get("pass_rate")
    )
