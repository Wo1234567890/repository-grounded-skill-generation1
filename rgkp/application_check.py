import argparse
import json
import time
from pathlib import Path

import yaml
from anthropic import Anthropic
from dotenv import load_dotenv

from utils.json_parser import parse_json_object
from rgkp.gap_discovery import load_trajectory

ROOT = Path(__file__).resolve().parents[1]

load_dotenv(ROOT / ".env")


def read_json(path):
    return json.loads(path.read_text())


def save_json(path, data):
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def check_application(payload, model, output_dir):

    prompt = (
        ROOT / "configs/prompts/application_check.txt"
    ).read_text()

    request = {
        "model": model,
        "system_prompt": prompt,
        "payload": payload
    }

    raw_path = output_dir / "raw_response.txt"
    input_path = output_dir / "input.json"
    metrics_path = output_dir / "metrics.json"

    if raw_path.exists():

        if read_json(input_path) != request:
            raise ValueError("Saved request mismatch")

        raw = raw_path.read_text()
        metrics = read_json(metrics_path)

        print("Reusing saved response.", flush=True)

    else:

        save_json(input_path, request)

        client = Anthropic(
            timeout=180,
            max_retries=0
        )

        start = time.monotonic()

        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=prompt,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(
                        payload,
                        ensure_ascii=False
                    )
                }
            ]
        )

        raw = "".join(
            block.text
            for block in response.content
            if block.type == "text"
        )

        metrics = {
            "model": response.model,
            "wall_time_sec": time.monotonic() - start,
            "stop_reason": response.stop_reason,
            "usage": response.usage.model_dump()
        }

        raw_path.write_text(raw)
        save_json(metrics_path, metrics)

    if metrics["stop_reason"] != "end_turn":
        raise ValueError("Incomplete model response")

    if metrics["model"] != model:
        raise ValueError("Model mismatch")

    return parse_json_object(raw)


def validate_results(result, payload):

    expected = {
        item["target_id"]: set(item["evidence_ids"])
        for item in payload["knowledge_targets"]
    }

    valid_lines = {
        event["line"]
        for event in payload["trajectory"]
    }

    seen = set()

    for item in result["application_results"]:

        target_id = item["target_id"]

        if target_id not in expected or target_id in seen:
            raise ValueError(f"Invalid target: {target_id}")

        seen.add(target_id)

        state = item["application_state"]
        guidance = item["execution_guidance"].strip()

        if state not in [
            "applied",
            "not_applied",
            "uncertain"
        ]:
            raise ValueError("Invalid application state")

        if not item["reason"].strip():
            raise ValueError("Missing application reason")

        cited_lines = set(item["trajectory_lines"])
        cited_evidence = set(item["evidence_ids"])

        if not cited_lines.issubset(valid_lines):
            raise ValueError("Invalid trajectory reference")

        if not cited_evidence.issubset(expected[target_id]):
            raise ValueError("Invalid repository evidence")

        if state == "not_applied":

            if not guidance:
                raise ValueError("Missing execution guidance")

            if not cited_lines or not cited_evidence:
                raise ValueError("Guidance without evidence")

            item["guidance_id"] = f"{target_id}_EG1"

        elif guidance:

            raise ValueError(
                "Unexpected execution guidance"
            )

    if seen != set(expected):
        raise ValueError("Missing application results")

    return result


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("--batch", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--run", required=True)

    args = parser.parse_args()

    relative = (
        Path(args.batch) / args.task / args.run
    )

    run_dir = (
        ROOT / "trajectories/no_skill" / relative
    )

    targets = read_json(
        ROOT / "rgkp/targets" / relative / "targets.json"
    )

    gaps = read_json(
        ROOT / "rgkp/gaps" / relative / "gaps.json"
    )

    evidence = read_json(
        ROOT / "rgkp/evidence" / relative / "evidence.json"
    )

    routing = read_json(
        ROOT / "rgkp/candidates" / relative
        / "routing/routing.json"
    )

    models = yaml.safe_load(
        (ROOT / "configs/models.yaml").read_text()
    )

    model = models["models"]["auxiliary_model"]["model_id"]

    target_map = {
        target["target_id"]: target
        for gap in targets["gap_results"]
        for target in gap["knowledge_targets"]
    }

    gap_map = {
        gap["gap_id"]: gap
        for gap in gaps["execution_gaps"]
    }

    evidence_map = {
        item["evidence_id"]: item
        for gap in evidence["gap_evidence"]
        for item in gap["evidence"]
    }

    output_dir = (
        ROOT / "rgkp/residual_skills"
        / relative / "guidance"
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "application_check.json"

    if output_path.exists():
        raise SystemExit("Application results already exist.")

    eligible = [
        item for item in routing["routing_results"]
        if item["route"] == "application_check"
        and item["baseline_state"] == 1
    ]

    print("Model:", model, flush=True)
    print("Eligible targets:", len(eligible), flush=True)

    if not eligible:

        save_json(
            output_path,
            {"application_results": []}
        )

        print("No eligible targets. No API call.")
        return

    selected_targets = []

    for item in eligible:

        target = target_map[item["target_id"]]

        selected_targets.append({
            "target_id": target["target_id"],
            "knowledge_target": target["knowledge_target"],
            "baseline_state": 1,
            "execution_gap": gap_map[item["gap_id"]],
            "evidence_ids": target["evidence_ids"],
            "supporting_evidence": [
                evidence_map[eid]
                for eid in target["evidence_ids"]
            ]
        })

    payload = {
        "task_description": (
            run_dir / "task.md"
        ).read_text(),

        "trajectory": load_trajectory(
            run_dir / "trajectory.jsonl"
        ),

        "environment_blockers": gaps[
            "environment_blockers"
        ],

        "knowledge_targets": selected_targets
    }

    if len(json.dumps(payload)) > 300000:
        raise ValueError(
            "Input too large; chunking required."
        )

    print("Checking knowledge application...", flush=True)

    result = check_application(
        payload, model, output_dir
    )

    result = validate_results(result, payload)

    save_json(output_path, result)

    for item in result["application_results"]:

        print("\nTarget:", item["target_id"])
        print("State:", item["application_state"])
        print("Reason:", item["reason"])

        if item["execution_guidance"]:
            print(
                "Guidance:",
                item["execution_guidance"]
            )

    print("\n===== Application Check Summary =====")

    for state in ["applied", "not_applied", "uncertain"]:

        count = sum(
            item["application_state"] == state
            for item in result["application_results"]
        )

        print(f"{state}: {count}")

    print("Guidance units:", sum(
        bool(item["execution_guidance"])
        for item in result["application_results"]
    ))

    print("Results saved to:", output_dir)


if __name__ == "__main__":
    main()
