import argparse
import json
import random
import time
from pathlib import Path

import yaml
from anthropic import Anthropic
from dotenv import load_dotenv

from utils.json_parser import parse_json_object

ROOT = Path(__file__).resolve().parents[1]

load_dotenv(ROOT / ".env")


def read_json(path):
    return json.loads(path.read_text())


def save_json(path, data):
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def generate_questions(payload):

    models = yaml.safe_load(
        (ROOT / "configs/models.yaml").read_text()
    )

    model = models["models"]["auxiliary_model"]["model_id"]

    system_prompt = (
        ROOT / "configs/prompts/diagnostic_question.txt"
    ).read_text()

    client = Anthropic()

    start = time.monotonic()

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": json.dumps(
                    payload, ensure_ascii=False
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


def validate_questions(result, targets):

    expected = {
        t["target_id"]: set(t["evidence_ids"])
        for t in targets
    }

    seen = set()

    for item in result["diagnostic_questions"]:

        target_id = item["target_id"]

        if target_id not in expected or target_id in seen:
            raise ValueError(f"Invalid target ID: {target_id}")

        seen.add(target_id)

        if item["status"] == "insufficient_evidence":
            if not item["reason"].strip():
                raise ValueError("Missing reason")
            continue

        if item["status"] != "ready":
            raise ValueError("Invalid question status")

        options = item["options"]

        if set(options) != {"A", "B", "C", "D"}:
            raise ValueError("Expected four options")

        normalized = {
            value.strip().casefold()
            for value in options.values()
        }

        if len(normalized) != 4 or "" in normalized:
            raise ValueError("Duplicate or empty options")

        answer = item["correct_answer"]

        if answer not in options:
            raise ValueError("Invalid reference answer")

        if not item["question"].strip():
            raise ValueError("Empty diagnostic question")

        if not item["reference_explanation"].strip():
            raise ValueError("Empty reference explanation")

        cited = set(item["evidence_ids"])

        if not cited or not cited.issubset(expected[target_id]):
            raise ValueError("Invalid supporting evidence")

        # Shuffle options using a reproducible seed.
        correct_text = options[answer]
        values = [options[k] for k in "ABCD"]

        rng = random.Random(f"rgkp-v1:{target_id}")
        rng.shuffle(values)

        item["options"] = dict(zip("ABCD", values))

        item["correct_answer"] = next(
            key for key, value in item["options"].items()
            if value == correct_text
        )

        item["question_id"] = f"{target_id}_Q1"

    if seen != set(expected):
        raise ValueError("Missing diagnostic questions")

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

    relative = Path(args.batch) / args.task / args.run

    run_dir = ROOT / "trajectories/no_skill" / relative

    target_data = read_json(
        ROOT / "rgkp/targets" / relative / "targets.json"
    )

    evidence_data = read_json(
        ROOT / "rgkp/evidence" / relative / "evidence.json"
    )

    evidence_map = {
        item["evidence_id"]: item
        for gap in evidence_data["gap_evidence"]
        for item in gap["evidence"]
    }

    targets = []

    for gap in target_data["gap_results"]:

        for target in gap["knowledge_targets"]:

            targets.append({
                "target_id": target["target_id"],
                "knowledge_target": target["knowledge_target"],
                "evidence_ids": target["evidence_ids"],
                "supporting_evidence": [
                    evidence_map[eid]
                    for eid in target["evidence_ids"]
                ]
            })

    output_dir = (
        ROOT / "rgkp/probes" / relative / "questions"
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    if (output_dir / "questions.json").exists():
        raise SystemExit("Questions already exist.")

    payload = {
        "task_description": (
            run_dir / "task.md"
        ).read_text(),
        "knowledge_targets": targets
    }

    print("Knowledge targets:", len(targets), flush=True)

    if not targets:
        save_json(
            output_dir / "questions.json",
            {"diagnostic_questions": []}
        )
        print("No targets. No API call.")
        return

    if args.reuse_response:

        if read_json(output_dir / "input.json") != payload:
            raise ValueError("Saved input does not match")

        raw = (output_dir / "raw_response.txt").read_text()
        metrics = read_json(output_dir / "metrics.json")

        print("Reusing saved response. No API call.")

    else:

        save_json(output_dir / "input.json", payload)

        print("Generating diagnostic questions...", flush=True)

        raw, metrics = generate_questions(payload)

        (output_dir / "raw_response.txt").write_text(raw)
        save_json(output_dir / "metrics.json", metrics)

    if metrics["stop_reason"] != "end_turn":
        raise ValueError("Model response may be incomplete")

    result = parse_json_object(raw)
    result = validate_questions(result, targets)

    save_json(output_dir / "questions.json", result)

    for item in result["diagnostic_questions"]:

        print("\nTarget:", item["target_id"])

        if item["status"] != "ready":
            print("Skipped:", item["reason"])
            continue

        print("Question:", item["question"])

        for key, value in item["options"].items():
            print(f"  {key}. {value}")

        print("Reference answer:", item["correct_answer"])

    print("\nResults saved to:", output_dir)


if __name__ == "__main__":
    main()
