import argparse
import json
import math
import subprocess

from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]


def git(repo, *args, allow_no_match=False):

    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300
    )

    if result.returncode != 0:
        if not (
            allow_no_match
            and result.returncode == 1
        ):
            raise RuntimeError(result.stderr)

    return result.stdout


def get_repository(task):

    if task["repo_source"] != "git":
        raise NotImplementedError(
            "Embedded repository retrieval not implemented yet."
        )

    owner, repo = (
        task["repo_url"].rstrip("/").split("/")[-2:]
    )

    repo = repo.removesuffix(".git")
    commit = task["repo_commit"]

    path = (
        ROOT / "repositories"
        / f"{owner}__{repo}__{commit}"
    )

    if not path.exists():
        raise FileNotFoundError(path)

    actual = git(path, "rev-parse", "HEAD").strip()

    if actual != commit:
        raise RuntimeError("Repository commit mismatch")

    return path


def retrieve_evidence(repo, gap, max_files=8):

    cues = gap["retrieval_cues"]

    files = git(
        repo, "ls-tree", "-r",
        "--name-only", "-z", "HEAD"
    ).split("\0")

    files = [f for f in files if f]

    path_cues = [
        x.strip().casefold()
        for x in cues.get("file_paths", [])
        if x.strip()
    ]

    name_cues = {
        Path(x).name.casefold()
        for x in cues.get("file_names", [])
        if x.strip()
    }

    terms = set()

    for key in [
        "functions_or_classes",
        "modules_or_packages",
        "commands",
        "error_messages",
        "tests",
        "configuration_terms",
        "other_keywords"
    ]:

        for term in cues.get(key, []):
            term = term.strip()

            if 2 <= len(term) <= 200:
                terms.add(term.casefold())

    terms = sorted(terms)

    scores = defaultdict(float)
    matched_terms = defaultdict(set)
    hits = defaultdict(dict)
    term_files = defaultdict(set)

    # Step 1: file path and name matching

    for path in files:

        lower = path.casefold()

        for cue in path_cues:

            if lower == cue or cue.endswith("/" + lower):
                scores[path] += 20

        if Path(path).name.casefold() in name_cues:
            scores[path] += 5

    # Step 2: content matching against original HEAD

    if terms:

        command = [
            "grep", "-n", "-I", "-i", "-F"
        ]

        for term in terms:
            command.extend(["-e", term])

        command.extend(["HEAD", "--"])

        output = git(
            repo, *command,
            allow_no_match=True
        )

        for line in output.splitlines():

            parts = line.split(":", 3)

            if len(parts) != 4:
                continue

            _, path, number, content = parts

            if not number.isdigit():
                continue

            found = {
                term for term in terms
                if term in content.casefold()
            }

            if not found:
                continue

            hits[path][int(number)] = found

            matched_terms[path].update(found)

            for term in found:
                term_files[term].add(path)

    # Step 3: rank by cue specificity

    weights = {
        term: 1 + math.log(
            (len(files) + 1)
            / (len(paths) + 1)
        )
        for term, paths in term_files.items()
    }

    for path, found in matched_terms.items():

        scores[path] += sum(
            weights[term] for term in found
        )

    ranked = sorted(
        scores,
        key=lambda path: (-scores[path], path)
    )

    # Step 4: extract original repository excerpts

    evidence = []

    for path in ranked:

        if len(evidence) >= max_files:
            break

        try:
            size = int(
                git(
                    repo, "cat-file", "-s",
                    f"HEAD:{path}"
                ).strip()
            )

            if size > 1000000:
                continue

            content = git(
                repo, "show", f"HEAD:{path}"
            )

        except (RuntimeError, ValueError):
            continue

        if "\0" in content:
            continue

        lines = content.split("\n")

        ranked_lines = sorted(
            hits[path],
            key=lambda n: (
                -sum(
                    weights[t]
                    for t in hits[path][n]
                ),
                n
            )
        )

        windows = []

        for number in ranked_lines:

            start = max(1, number - 8)
            end = min(len(lines), number + 8)

            if any(
                start <= b and end >= a
                for a, b in windows
            ):
                continue

            windows.append((start, end))

            if len(windows) >= 3:
                break

        if not windows:
            windows = [(1, min(len(lines), 35))]

        for start, end in windows:

            selected = lines[start - 1:end]

            snippet = "\n".join(
                f"{i}: {line[:500]}"
                for i, line in enumerate(
                    selected, start
                )
            )

            evidence.append({
                "evidence_id": (
                    f"{gap['gap_id']}_E"
                    f"{len(evidence) + 1}"
                ),
                "file_path": path,
                "start_line": start,
                "end_line": end,
                "retrieval_score": round(
                    scores[path], 3
                ),
                "matched_cues": sorted(
                    matched_terms[path]
                ),
                "content": snippet,
                "line_truncated": any(
                    len(line) > 500
                    for line in selected
                )
            })

            if len(evidence) >= max_files:
                break

    return evidence


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("--batch", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument(
        "--max-evidence", type=int, default=8
    )

    args = parser.parse_args()

    relative = (
        Path(args.batch) / args.task / args.run
    )

    run_dir = (
        ROOT / "trajectories/no_skill" / relative
    )

    gaps_path = (
        ROOT / "rgkp/gaps" / relative / "gaps.json"
    )

    output_dir = (
        ROOT / "rgkp/evidence" / relative
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    task = json.loads(
        (run_dir / "metadata.json").read_text()
    )

    gaps = json.loads(gaps_path.read_text())
    repo = get_repository(task)

    print("Task:", task["instance_id"])
    print("Repository:", repo)
    print("Commit:", task["repo_commit"])

    results = []

    for gap in gaps["execution_gaps"]:

        evidence = retrieve_evidence(
            repo,
            gap,
            max_files=args.max_evidence
        )

        results.append({
            "gap_id": gap["gap_id"],
            "evidence": evidence
        })

        print(
            f"\n{gap['gap_id']}: "
            f"{len(evidence)} evidence snippets"
        )

        for item in evidence:
            print(
                f"  {item['evidence_id']} | "
                f"{item['file_path']}:"
                f"{item['start_line']}-"
                f"{item['end_line']}"
            )

    output = {
        "instance_id": task["instance_id"],
        "repository_url": task["repo_url"],
        "repository_commit": task["repo_commit"],
        "gap_evidence": results
    }

    path = output_dir / "evidence.json"

    path.write_text(
        json.dumps(
            output,
            indent=2,
            ensure_ascii=False
        )
    )

    print("\nResults saved to:", path)


if __name__ == "__main__":
    main()
