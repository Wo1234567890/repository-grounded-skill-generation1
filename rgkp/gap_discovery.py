import argparse
import json
import time
from pathlib import Path

import yaml
from anthropic import Anthropic
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

load_dotenv(ROOT / ".env")


def load_trajectory(path):

    events = []

    with path.open(encoding="utf-8") as f:

        for line_number, line in enumerate(f, 1):

            if not line.strip():
                continue

            event = json.loads(line)
            event_type = event.get("type")

            if event_type in ["assistant", "user"]:

                content = event.get(
                    "message", {}
                ).get("content", [])

                if isinstance(content, str):
                    content = [
                        {"type": "text", "text": content}
                    ]

                selected = [
                    block for block in content
                    if block.get("type") in [
                        "text",
                        "tool_use",
                        "tool_result"
                    ]
                ]

                if selected:
                    events.append({
                        "line": line_number,
                        "type": event_type,
                        "content": selected
                    })

            elif event_type == "result":

                events.append({
                    "line": line_number,
                    "type": "result",
                    "result": event.get("result"),
                    "is_error": event.get("is_error"),
                    "terminal_reason": event.get(
                        "terminal_reason"
                    ),
                    "permission_denials": event.get(
                        "permission_denials", []
                    )
                })

    return events


def discover_gaps(task, trajectory, feedback=""):

    models = yaml.safe_load(
        (ROOT / "configs/models.yaml").read_text()
    )

    model = models["models"]["auxiliary_model"]["model_id"]

    system_prompt = (
        ROOT / "configs/prompts/gap_discovery.txt"
    ).read_text()

    user_input = json.dumps({
        "task_description": task,
        "trajectory": trajectory,
        "allowed_trajectory_lines": [
            event["line"] for event in trajectory
        ],
        "validation_feedback": feedback
    }, ensure_ascii=False)

    if len(user_input) > 300000:
        raise ValueError(
            "Trajectory too large; chunking required."
        )

    client = Anthropic()

    start = time.monotonic()

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": user_input
            }
        ]
    )

    wall_time = time.monotonic() - start

    raw = "".join(
        block.text
        for block in response.content
        if block.type == "text"
    ).strip()

    metrics = {
        "model": response.model,
        "wall_time_sec": wall_time,
        "stop_reason": response.stop_reason,
        "usage": response.usage.model_dump()
    }

    return raw, metrics



def validate_response(raw, metrics, trajectory):
    from utils.json_parser import parse_json_object

    if metrics.get("stop_reason") != "end_turn":
        raise ValueError("Incomplete model response")

    result = parse_json_object(raw)

    gaps = result["execution_gaps"]
    blockers = result["environment_blockers"]

    if not isinstance(gaps, list):
        raise ValueError("execution_gaps must be a list")

    if not isinstance(blockers, list):
        raise ValueError("environment_blockers must be a list")

    if len(gaps) > 3:
        raise ValueError("Return at most 3 execution gaps")

    valid_lines = {
        event["line"] for event in trajectory
    }

    for gap in gaps:
        evidence = gap["trajectory_evidence"]

        if not evidence:
            raise ValueError("Gap has no evidence")

        for item in evidence:
            line = item["line"]

            if type(line) is not int or line not in valid_lines:
                raise ValueError(
                    f"Invalid trajectory evidence line: {line}"
                )

    for blocker in blockers:
        lines = blocker["evidence_lines"]

        if not lines:
            raise ValueError("Blocker has no evidence")

        for line in lines:
            if type(line) is not int or line not in valid_lines:
                raise ValueError(
                    f"Invalid blocker evidence line: {line}"
                )

    return result


def archive_attempt(output_dir, raw, metrics):
    history = output_dir / "attempts"
    history.mkdir(exist_ok=True)

    index = 1

    while (history / f"attempt_{index:03d}").exists():
        index += 1

    folder = history / f"attempt_{index:03d}"
    folder.mkdir()

    (folder / "raw_response.txt").write_text(raw)

    (folder / "metrics.json").write_text(
        json.dumps(metrics, indent=2)
    )

    return folder


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("--batch", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--reuse-response", action="store_true")

    args = parser.parse_args()

    run_dir = (
        ROOT / "trajectories/no_skill"
        / args.batch
        / args.task
        / args.run
    )

    output_dir = (
        ROOT / "rgkp/gaps"
        / args.batch
        / args.task
        / args.run
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    if (output_dir / "gaps.json").exists():
        raise SystemExit(
            "Gap results already exist."
        )

    task = (run_dir / "task.md").read_text()

    trajectory = load_trajectory(
        run_dir / "trajectory.jsonl"
    )

    print("Task:", args.task, flush=True)
    print("Run:", args.run, flush=True)
    print("Trajectory events:", len(trajectory), flush=True)
    print("Discovering execution gaps...", flush=True)


    raw_path = output_dir / "raw_response.txt"
    metrics_path = output_dir / "metrics.json"

    if args.reuse_response:
        raw = raw_path.read_text()
        metrics = json.loads(metrics_path.read_text())

        print("Reusing saved response. No API call.")

        result = validate_response(
            raw, metrics, trajectory
        )

    else:
        # Preserve the original failed response.
        if (
            raw_path.exists()
            and metrics_path.exists()
            and not (output_dir / "attempts").exists()
        ):
            archive_attempt(
                output_dir,
                raw_path.read_text(),
                json.loads(metrics_path.read_text())
            )

        feedback = ""

        for attempt in range(1, 4):
            print(f"Gap Discovery attempt {attempt}/3")

            raw, metrics = discover_gaps(
                task, trajectory, feedback
            )

            folder = archive_attempt(
                output_dir, raw, metrics
            )

            raw_path.write_text(raw)
            metrics_path.write_text(
                json.dumps(metrics, indent=2)
            )

            try:
                result = validate_response(
                    raw, metrics, trajectory
                )
                break

            except (ValueError, KeyError, TypeError) as error:
                feedback = str(error)

                (folder / "validation_error.txt").write_text(
                    feedback
                )

                print("Validation failed:", feedback)

                if attempt == 3:
                    raise

    (output_dir / "gaps.json").write_text(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False
        )
    )

    print("\nExecution gaps:",
          len(result["execution_gaps"]))

    print("Environment blockers:",
          len(result["environment_blockers"]))

    print(json.dumps(
        result,
        indent=2,
        ensure_ascii=False
    ))

    print("\nResults saved to:", output_dir)


if __name__ == "__main__":
    main()
