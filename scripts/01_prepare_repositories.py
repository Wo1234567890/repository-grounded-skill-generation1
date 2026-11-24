import argparse
import json
import os
import shutil
import subprocess

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
REPOS = ROOT / "repositories"
LOGS = ROOT / "logs/repository_preparation"

REPOS.mkdir(exist_ok=True)
LOGS.mkdir(parents=True, exist_ok=True)

parser = argparse.ArgumentParser()
parser.add_argument("--task", default=None)
parser.add_argument("--workers", type=int, default=2)
args = parser.parse_args()

tasks = []

for batch in ["batch1", "batch2"]:
    path = ROOT / "metadata" / f"{batch}.json"
    tasks.extend(json.loads(path.read_text()))

if args.task:
    tasks = [
        t for t in tasks
        if t["task_id"] == args.task
        or t["instance_id"] == args.task
    ]

    if not tasks:
        raise SystemExit("Task not found")

unique = {}

for task in tasks:
    if task["repo_source"] != "git":
        continue

    key = (task["repo_url"], task["repo_commit"])
    unique[key] = task


def prepare(task):

    url = task["repo_url"]
    commit = task["repo_commit"]

    owner, repo = url.rstrip("/").split("/")[-2:]
    repo = repo.removesuffix(".git")

    name = f"{owner}__{repo}__{commit}"
    destination = REPOS / name
    temporary = REPOS / f".{name}.partial"
    log_path = LOGS / f"{name}.log"

    def git(*cmd, cwd, log):
        return subprocess.run(
            ["git", *cmd],
            cwd=cwd,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=1800,
            env={
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0"
            }
        )

    try:

        if destination.exists():

            actual = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=destination,
                text=True
            ).strip()

            status = subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=destination,
                text=True
            ).strip()

            if actual == commit and not status:
                print(f"[SKIP] {owner}/{repo}", flush=True)
                return True

            raise RuntimeError(
                "Existing snapshot is invalid or modified"
            )

        if temporary.exists():
            shutil.rmtree(temporary)

        temporary.mkdir()

        print(f"[START] {owner}/{repo}", flush=True)

        with log_path.open("w") as log:

            git("init", cwd=temporary, log=log)

            git(
                "config", "core.autocrlf", "false",
                cwd=temporary, log=log
            )

            git(
                "remote", "add", "origin", url,
                cwd=temporary, log=log
            )

            git(
                "fetch", "--depth=1", "origin", commit,
                cwd=temporary, log=log
            )

            git(
                "checkout", "--detach", "FETCH_HEAD",
                cwd=temporary, log=log
            )

        actual = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=temporary,
            text=True
        ).strip()

        if actual != commit:
            raise RuntimeError("Commit mismatch")

        status = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=temporary,
            text=True
        ).strip()

        if status:
            raise RuntimeError("Snapshot is not clean")

        temporary.rename(destination)

        print(f"[OK] {owner}/{repo}", flush=True)
        return True

    except Exception as error:

        print(
            f"[FAILED] {owner}/{repo}: {error}",
            flush=True
        )

        with log_path.open("a") as log:
            log.write(f"\nERROR: {error}\n")

        return False


print(f"Unique snapshots: {len(unique)}")
print(f"Parallel workers: {args.workers}")

with ThreadPoolExecutor(max_workers=args.workers) as pool:
    results = list(pool.map(prepare, unique.values()))

print("\n===== Summary =====")
print(f"Total: {len(results)}")
print(f"Successful: {sum(results)}")
print(f"Failed: {len(results) - sum(results)}")

raise SystemExit(0 if all(results) else 1)
