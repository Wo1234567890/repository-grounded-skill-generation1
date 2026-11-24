import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

SOURCE = (
    Path.home()
    / "Desktop/skill-generation/experiment2/config"
)

OUTPUT = ROOT / "metadata"
OUTPUT.mkdir(exist_ok=True)


def find_tasks(data):
    if isinstance(data, dict):
        if "id" in data and "repo" in data:
            yield data

        for value in data.values():
            yield from find_tasks(value)

    elif isinstance(data, list):
        for item in data:
            yield from find_tasks(item)


for batch in ["batch1", "batch2"]:

    config_path = SOURCE / f"benchmark_{batch}_final.yaml"

    with config_path.open() as f:
        config = yaml.safe_load(f)

    task_files = sorted(
        (ROOT / "tasks" / batch).glob("*.md")
    )

    config_tasks = {}

    for item in find_tasks(config):
        task_id = item["id"]

        if task_id in config_tasks:
            raise ValueError(f"Duplicate task: {task_id}")

        config_tasks[task_id] = item

    metadata = []

    for task_file in task_files:

        task_id = task_file.stem

        if task_id not in config_tasks:
            print(f"MISSING: {batch}/{task_id}")
            continue

        item = config_tasks[task_id]
        repo = item.get("repo", {})
        env = item.get("environment", {})
        limits = env.get("limits", {})

        metadata.append({
            "instance_id": f"{batch}/{task_id}",
            "task_id": task_id,
            "batch": batch,
            "task_file": str(
                task_file.relative_to(ROOT)
            ),
            "repo_url": repo.get("url"),
            "repo_commit": repo.get("commit"),
            "sparse_checkout": repo.get(
                "sparse_checkout", []
            ),
            "docker_image": env.get("base_image"),
            "timeout_sec": limits.get(
                "total_timeout_sec"
            ),
            "setup_commands": env.get(
                "setup_commands", []
            )
        })

    output_path = OUTPUT / f"{batch}.json"

    output_path.write_text(
        json.dumps(
            metadata,
            indent=2,
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    print(f"{batch}: {len(metadata)}/{len(task_files)} tasks")
    print(f"Saved: {output_path}")

    for item in metadata:
        for field in [
            "repo_url",
            "repo_commit",
            "docker_image"
        ]:
            if not item[field]:
                print(
                    f"WARNING: {item['instance_id']} "
                    f"missing {field}"
                )
