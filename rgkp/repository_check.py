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


def check_repository(target, evidence, model, output_dir):

    if not evidence:
        return {
            "repository_provides": False,
            "supporting_evidence_ids": [],
            "reason": "No relevant prescriptive evidence retrieved."
        }

    payload = {
        "knowledge_target": target["knowledge_target"],
        "prescriptive_evidence": evidence
    }

    prompt = (
        ROOT / "configs/prompts/repository_check.txt"
    ).read_text()

    save_json(output_dir / "input.json", payload)

    client = Anthropic()
    start = time.monotonic()

    response = client.messages.create(
        model=model,
        max_tokens=2048,
        system=prompt,
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

    (output_dir / "raw_response.txt").write_text(raw)

    save_json(
        output_dir / "metrics.json",
        {
            "model": response.model,
            "wall_time_sec": time.monotonic() - start,
            "stop_reason": response.stop_reason,
            "usage": response.usage.model_dump()
        }
    )

    if response.stop_reason != "end_turn":
        raise ValueError("Incomplete model response")

    result = parse_json_object(raw)

    if type(result["repository_provides"]) is not bool:
        raise ValueError("Invalid repository check result")

    allowed_ids = {
        e["evidence_id"] for e in evidence
    }

    cited_ids = set(result["supporting_evidence_ids"])

    if not cited_ids.issubset(allowed_ids):
        raise ValueError("Invalid evidence references")

    if result["repository_provides"] and not cited_ids:
        raise ValueError("Missing supporting evidence")

    return result


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("--batch", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--run", required=True)

    args = parser.parse_args()

    relative = Path(args.batch) / args.task / args.run

    targets = read_json(
        ROOT / "rgkp/targets" / relative / "targets.json"
    )

    evidence = read_json(
        ROOT / "rgkp/evidence" / relative / "evidence.json"
    )

    baseline = read_json(
        ROOT / "rgkp/probes" / relative
        / "baseline/baseline.json"
    )

    models = yaml.safe_load(
        (ROOT / "configs/models.yaml").read_text()
    )

    model = models["models"]["auxiliary_model"]["model_id"]

    baseline_map = {
        item["target_id"]: item
        for item in baseline["baseline_results"]
    }

    evidence_map = {
        item["evidence_id"]: item
        for gap in evidence["gap_evidence"]
        for item in gap["evidence"]
    }

    output_dir = (
        ROOT / "rgkp/candidates" / relative / "routing"
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    if (output_dir / "routing.json").exists():
        raise SystemExit("Routing results already exist.")

    results = []

    for gap in targets["gap_results"]:

        prescriptive = [
            evidence_map[item["evidence_id"]]
            for item in gap["evidence_classification"]
            if item["type"] == "prescriptive"
        ]

        for target in gap["knowledge_targets"]:

            target_id = target["target_id"]

            state = baseline_map[target_id]["baseline_state"]

            record = {
                "target_id": target_id,
                "gap_id": gap["gap_id"],
                "baseline_state": state,
                "repository_provides": None,
                "repository_check": None
            }

            if state == 1:

                record["route"] = "application_check"

            elif state is None:

                record["route"] = "unresolved"

            elif state == 0:

                target_dir = output_dir / target_id
                target_dir.mkdir(exist_ok=True)

                check = check_repository(
                    target,
                    prescriptive,
                    model,
                    target_dir
                )

                save_json(
                    target_dir / "check.json",
                    check
                )

                record["repository_check"] = check

                record["repository_provides"] = (
                    check["repository_provides"]
                )

                if check["repository_provides"]:
                    record["route"] = "repository_redundant"
                else:
                    record["route"] = "candidate_generation"

            else:
                raise ValueError("Invalid baseline state")

            results.append(record)

            print(
                f"{target_id}: "
                f"B={state} -> {record['route']}",
                flush=True
            )

    save_json(
        output_dir / "routing.json",
        {"routing_results": results}
    )

    print("\n===== Routing Summary =====")

    for route in [
        "application_check",
        "repository_redundant",
        "candidate_generation",
        "unresolved"
    ]:

        count = sum(
            item["route"] == route
            for item in results
        )

        print(f"{route}: {count}")

    print("Results saved to:", output_dir)


if __name__ == "__main__":
    main()
