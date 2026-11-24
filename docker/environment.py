import json
import subprocess
import uuid

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DockerEnvironment:

    def __init__(self, task):
        self.task = task
        self.image = task["docker_image"]
        self.name = f"rgkp-{uuid.uuid4().hex[:12]}"
        self.created = False

        image_info = json.loads(
            self.docker("image", "inspect", self.image)
        )[0]

        self.image_id = image_info["Id"]
        self.platform = (
            f"{image_info['Os']}/"
            f"{image_info['Architecture']}"
        )

        if task["repo_source"] == "docker_image":
            self.repo_path = task["repo_path"]
            self.snapshot = None
        else:
            owner, repo = (
                task["repo_url"].rstrip("/").split("/")[-2:]
            )
            repo = repo.removesuffix(".git")
            commit = task["repo_commit"]

            self.snapshot = (
                ROOT / "repositories"
                / f"{owner}__{repo}__{commit}"
            )

            self.repo_path = f"/workspace/{repo}"

    def docker(self, *args, timeout=600):
        result = subprocess.run(
            ["docker", *args],
            capture_output=True,
            text=True,
            timeout=timeout
        )

        if result.returncode != 0:
            raise RuntimeError(
                result.stderr.strip()
                or result.stdout.strip()
            )

        return result.stdout.strip()

    def start(self):

        if self.snapshot and not self.snapshot.exists():
            raise FileNotFoundError(self.snapshot)

        self.docker(
            "create",
            "--name", self.name,
            "--pull=never",
            "--platform", self.platform,
            "--network", "bridge",
            "--entrypoint", "/bin/sh",
            self.image_id,
            "-c", "while :; do sleep 3600; done"
        )

        self.created = True

        try:
            self.docker("start", self.name)

            if self.snapshot:

                self.docker(
                    "exec", "-u", "0", self.name,
                    "mkdir", "-p", "/workspace"
                )

                self.docker(
                    "exec", self.name,
                    "test", "!", "-e", self.repo_path
                )

                self.docker(
                    "cp",
                    str(self.snapshot),
                    f"{self.name}:{self.repo_path}"
                )

                uid = self.docker(
                    "exec", self.name, "id", "-u"
                )
                gid = self.docker(
                    "exec", self.name, "id", "-g"
                )

                self.docker(
                    "exec", "-u", "0", self.name,
                    "chown", "-R",
                    f"{uid}:{gid}", self.repo_path
                )

            self.docker(
                "exec", self.name,
                "test", "-d", self.repo_path
            )

        except Exception:
            self.stop()
            raise

        return self

    def execute(self, command, timeout=300):

        result = subprocess.run(
            [
                "docker", "exec",
                "-w", self.repo_path,
                self.name,
                "/bin/sh", "-c", command
            ],
            capture_output=True,
            text=True,
            timeout=timeout
        )

        return {
            "command": command,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr
        }

    def stop(self):
        if self.created:
            self.docker("rm", "-f", self.name)
            self.created = False

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()


if __name__ == "__main__":

    tasks = json.loads(
        (ROOT / "metadata/batch1.json").read_text()
    )

    task = next(
        t for t in tasks
        if t["task_id"] == "gitops-workflow"
    )

    with DockerEnvironment(task) as env:

        print("Container:", env.name)
        print("Image:", env.image)
        print("Image ID:", env.image_id)
        print("Platform:", env.platform)
        print("Repository:", env.repo_path)

        for command in [
            "pwd",
            "git rev-parse HEAD",
            "git status --porcelain",
            "ls -la | head -15",
            "go version",
            "test -w ."
        ]:
            result = env.execute(command)

            print(json.dumps(
                result, indent=2,
                ensure_ascii=False
            ))

            if result["exit_code"] != 0:
                raise RuntimeError(
                    f"Smoke test failed: {command}"
                )

            if command == "git rev-parse HEAD":
                if result["stdout"].strip() != task["repo_commit"]:
                    raise RuntimeError("Commit mismatch")

            if command == "git status --porcelain":
                if result["stdout"].strip():
                    raise RuntimeError("Repository is not clean")

    print("Docker environment test completed.")
    print("Container removed.")
