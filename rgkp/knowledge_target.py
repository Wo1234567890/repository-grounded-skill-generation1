import argparse
import json
import time
from pathlib import Path

import yaml
from anthropic import Anthropic
from dotenv import load_dotenv

from utils.json_parser import parse_json_object
from utils.validated_generation import generate_validated

ROOT = Path(__file__).resolve().parents[1]

load_dotenv(ROOT / ".env")


def read_json(path):
    return json.loads(path.read_text())


def save_json(path, data):
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def construct_targets(payload, feedback=""):

    models = yaml.safe_load(
        (ROOT / "configs/models.yaml").read_text()
    )

    model = models["models"]["auxiliary_model"]["model_id"]

    prompt = (
        ROOT / "configs/prompts/knowledge_target.txt"
    ).read_text()

    client = Anthropic()

    start = time.monotonic()

    response = client.messages.create(
        model=model,
        max_tokens=8192,
        system=prompt,
        messages=[
            {
                "role": "user",
                "content": json.dumps(
                    {
                        **payload,
                        "expected_classification_ids": {
                            item["gap_id"]: [
                                e["evidence_id"]
                                for e in item["evidence"]
                            ]
                            for item in payload["repository_evidence"]
                        },
                        "validation_feedback": feedback
                    }, ensure_ascii=False
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

    return raw, metrics


def validate_targets(result, payload):

    evidence_by_gap = {
        item["gap_id"]: {
            e["evidence_id"]
            for e in item["evidence"]
        }
        for item in payload["repository_evidence"]
    }

    expected_gaps = {
        gap["gap_id"]
        for gap in payload["execution_gaps"]
    }

    max_targets = payload["max_targets_per_gap"]

    seen = set()

    for item in result["gap_results"]:

        gap_id = item["gap_id"]

        if gap_id not in expected_gaps or gap_id in seen:
            raise ValueError(f"Invalid gap ID: {gap_id}")

        seen.add(gap_id)

        assessment = item["assessment"]

        if assessment not in [
            "grounded",
            "insufficient_evidence",
            "environment_only"
        ]:
            raise ValueError("Invalid gap assessment")

        classifications = {}

        for e in item["evidence_classification"]:

            eid = e["evidence_id"]
            kind = e["type"]

            if eid not in evidence_by_gap[gap_id]:
                raise ValueError(f"Unknown evidence: {eid}")

            if eid in classifications:
                raise ValueError(f"Duplicate evidence: {eid}")

            if kind not in [
                "prescriptive",
                "descriptive",
                "irrelevant"
            ]:
                raise ValueError("Invalid evidence type")

            classifications[eid] = kind

        if set(classifications) != evidence_by_gap[gap_id]:
            missing = sorted(
                evidence_by_gap[gap_id] - set(classifications)
            )
            raise ValueError(
                f"{gap_id}: Missing evidence classification: {missing}"
            )

        targets = item["knowledge_targets"]

        if len(targets) > max_targets:
            raise ValueError("Too many knowledge targets")

        if assessment != "grounded" and targets:
            raise ValueError("Targets without grounded evidence")

        for index, target in enumerate(targets, 1):

            target["target_id"] = f"{gap_id}_KT{index}"

            for field in [
                "knowledge_target",
                "target_descriptor",
                "rationale"
            ]:
                if not target[field].strip():
                    raise ValueError(f"Empty field: {field}")

            if not target["evidence_ids"]:
                raise ValueError("Target without evidence")

            for eid in target["evidence_ids"]:
                if classifications.get(eid) not in [
                    "prescriptive",
                    "descriptive"
                ]:
                    raise ValueError(
                        f"Unsupported target evidence: {eid}"
                    )

    if seen != expected_gaps:
        raise ValueError("Missing gap results")

    return result


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("--batch", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument(
        "--reuse-response", action="store_true"
    )

    args = parser.parse_args()

    relative = (
        Path(args.batch) / args.task / args.run
    )

    run_dir = ROOT / "trajectories/no_skill" / relative

    gaps = read_json(
        ROOT / "rgkp/gaps" / relative / "gaps.json"
    )

    evidence = read_json(
        ROOT / "rgkp/evidence" / relative / "evidence.json"
    )

    config = yaml.safe_load(
        (ROOT / "configs/experiment.yaml").read_text()
    )

    output_dir = ROOT / "rgkp/targets" / relative
    output_dir.mkdir(parents=True, exist_ok=True)

    if (output_dir / "targets.json").exists():
        raise SystemExit("Knowledge targets already exist.")

    payload = {
        "task_description": (
            run_dir / "task.md"
        ).read_text(),
        "execution_gaps": gaps["execution_gaps"],
        "environment_blockers": gaps["environment_blockers"],
        "repository_evidence": evidence["gap_evidence"],
        "max_targets_per_gap": config[
            "gap_grounding"
        ]["max_knowledge_targets_per_gap"]
    }

    print("Task:", args.task, flush=True)
    print("Run:", args.run, flush=True)
    print("Constructing knowledge targets...", flush=True)

    if not payload["execution_gaps"]:

        result = {"gap_results": []}
        save_json(output_dir / "targets.json", result)

        print("No execution gaps. No API call.")
        return


    if args.reuse_response:

        if read_json(output_dir / "input.json") != payload:
            raise ValueError("Saved input does not match")

        raw = (output_dir / "raw_response.txt").read_text()
        metrics = read_json(output_dir / "metrics.json")

        print("Reusing saved response. No API call.")

        if metrics["stop_reason"] != "end_turn":
            raise ValueError("Model response may be incomplete")

        result = validate_targets(
            parse_json_object(raw), payload
        )

    else:

        input_path = output_dir / "input.json"

        if input_path.exists():
            if read_json(input_path) != payload:
                raise ValueError("Saved input does not match")
        else:
            save_json(input_path, payload)

        result = generate_validated(
            generate=lambda feedback: construct_targets(
                payload, feedback
            ),
            validator=lambda result: validate_targets(
                result, payload
            ),
            output_dir=output_dir,
            max_attempts=3
        )

    result["repository_commit"] = evidence["repository_commit"]

    save_json(output_dir / "targets.json", result)

    total = 0

    for item in result["gap_results"]:

        targets = item["knowledge_targets"]
        total += len(targets)

        print(f"\n{item['gap_id']}:")
        print("Assessment:", item["assessment"])

        for target in targets:
            print(
                target["target_id"],
                target["knowledge_target"]
            )

    print("\nTotal knowledge targets:", total)
    print("Results saved to:", output_dir)


if __name__ == "__main__":
    main()
