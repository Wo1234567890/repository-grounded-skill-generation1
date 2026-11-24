import argparse
import json
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


def generate_candidates(payload, model, output_dir):

    prompt = (
        ROOT / "configs/prompts/candidate_generation.txt"
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
            raise ValueError("Saved request does not match")

        raw = raw_path.read_text()
        metrics = read_json(metrics_path)

        print("Reusing saved response.", flush=True)

    else:

        save_json(input_path, request)

        client = Anthropic()
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


def validate_candidates(result, target_id, count):

    candidates = result["candidates"]

    if len(candidates) != count:
        raise ValueError("Incorrect candidate count")

    seen = set()
    validated = []

    for index, candidate in enumerate(candidates, 1):

        knowledge = candidate["knowledge"].strip()

        if not knowledge:
            raise ValueError("Empty candidate")

        normalized = " ".join(
            knowledge.casefold().split()
        )

        if normalized in seen:
            raise ValueError("Duplicate candidate")

        seen.add(normalized)

        validated.append({
            "candidate_id": f"{target_id}_C{index}",
            "target_id": target_id,
            "knowledge": knowledge
        })

    return validated


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

    config = yaml.safe_load(
        (ROOT / "configs/experiment.yaml").read_text()
    )

    models = yaml.safe_load(
        (ROOT / "configs/models.yaml").read_text()
    )

    model = models["models"][
        "candidate_generator"
    ]["model_id"]

    count = config[
        "candidate_generation"
    ]["candidates_per_target"]

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

    eligible = [
        item for item in routing["routing_results"]
        if item["route"] == "candidate_generation"
    ]

    output_dir = (
        ROOT / "rgkp/candidates"
        / relative / "generation"
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    print("Model:", model, flush=True)
    print("Eligible targets:", len(eligible), flush=True)
    print("Candidates per target:", count, flush=True)

    results = []

    for item in eligible:

        target_id = item["target_id"]
        target = target_map[target_id]
        gap = gap_map[item["gap_id"]]

        # Explicit input allowlist.
        payload = {
            "task_context": (
                run_dir / "task.md"
            ).read_text(),

            "execution_gap": {
                "local_state": gap["local_state"],
                "agent_action": gap["agent_action"],
                "observed_problem": gap["observed_problem"]
            },

            "target_descriptor": target[
                "target_descriptor"
            ],

            "relevant_repository_evidence": [
                {
                    "file_path": evidence_map[eid]["file_path"],
                    "content": evidence_map[eid]["content"]
                }
                for eid in target["evidence_ids"]
            ],

            "required_candidate_count": count
        }

        target_dir = output_dir / target_id
        target_dir.mkdir(exist_ok=True)

        print(
            f"\nGenerating candidates for {target_id}...",
            flush=True
        )

        raw_result = generate_candidates(
            payload, model, target_dir
        )

        candidates = validate_candidates(
            raw_result, target_id, count
        )

        save_json(
            target_dir / "candidates.json",
            {"candidates": candidates}
        )

        results.extend(candidates)

        for candidate in candidates:
            print(
                candidate["candidate_id"],
                candidate["knowledge"]
            )

    save_json(
        output_dir / "candidates.json",
        {
            "model": model,
            "candidates_per_target": count,
            "eligible_targets": len(eligible),
            "candidates": results
        }
    )

    print("\n===== Candidate Generation Summary =====")
    print("Eligible targets:", len(eligible))
    print("Generated candidates:", len(results))

    if not eligible:
        print("No eligible targets. No API call.")

    print("Results saved to:", output_dir)


if __name__ == "__main__":
    main()
