import argparse
import json
import time
from pathlib import Path

import yaml
from anthropic import Anthropic
from dotenv import load_dotenv

from rgkp.baseline_probing import probe_question

ROOT = Path(__file__).resolve().parents[1]

load_dotenv(ROOT / ".env")


def read_json(path):
    return json.loads(path.read_text())


def save_json(path, data):
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def count_candidate_tokens(knowledge, model, output_dir):

    path = output_dir / "token_count.json"

    if path.exists():

        record = read_json(path)

        if (
            record["model"] != model
            or record["knowledge"] != knowledge
        ):
            raise ValueError("Token count input mismatch")

        return record["input_tokens"]

    client = Anthropic(
        timeout=120,
        max_retries=0
    )

    start = time.monotonic()

    response = client.messages.count_tokens(
        model=model,
        messages=[
            {
                "role": "user",
                "content": knowledge
            }
        ]
    )

    record = {
        "model": model,
        "knowledge": knowledge,
        "input_tokens": response.input_tokens,
        "wall_time_sec": time.monotonic() - start
    }

    save_json(path, record)

    return response.input_tokens


def evaluate_candidate(
    candidate,
    question,
    model,
    repetitions,
    output_dir
):

    candidate_id = candidate["candidate_id"]
    knowledge = candidate["knowledge"]

    candidate_dir = output_dir / candidate_id
    candidate_dir.mkdir(parents=True, exist_ok=True)

    expected_input = {
        "question": question["question"],
        "options": question["options"],
        "candidate_knowledge": knowledge
    }

    records = []

    print(f"\nCandidate: {candidate_id}", flush=True)

    for index in range(1, repetitions + 1):

        path = candidate_dir / f"probe_{index}.json"

        if path.exists():

            record = read_json(path)

            if (
                record["input"] != expected_input
                or record["model"] != model
            ):
                raise ValueError(
                    "Saved intervention input mismatch"
                )

            print(
                f"Probe {index}: reusing saved result",
                flush=True
            )

        else:

            record = probe_question(
                question,
                model,
                candidate=knowledge
            )

            save_json(path, record)

        record["correct"] = (
            record["answer"] == question["correct_answer"]
            if record["valid"]
            else None
        )

        records.append(record)

        print(
            f"Probe {index}: "
            f"{record['answer']} | "
            f"correct={record['correct']}",
            flush=True
        )

    valid_count = sum(
        r["valid"] for r in records
    )

    correct_count = sum(
        r["correct"] is True for r in records
    )

    if valid_count != repetitions:

        intervention_state = None
        delta = None

    else:

        intervention_state = int(
            correct_count > repetitions / 2
        )

        # All eligible targets have baseline state 0.
        delta = intervention_state

    token_count = None

    if delta == 1:

        token_count = count_candidate_tokens(
            knowledge,
            model,
            candidate_dir
        )

    result = {
        "candidate_id": candidate_id,
        "target_id": candidate["target_id"],
        "question_id": question["question_id"],
        "knowledge": knowledge,
        "baseline_state": 0,
        "intervention_state": intervention_state,
        "delta": delta,
        "answers": [
            r["answer"] for r in records
        ],
        "valid_count": valid_count,
        "correct_count": correct_count,
        "repetitions": repetitions,
        "candidate_tokens": token_count
    }

    save_json(
        candidate_dir / "summary.json",
        result
    )

    print(
        f"Intervention state: {intervention_state}",
        flush=True
    )

    print(f"Delta: {delta}", flush=True)

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

    candidate_data = read_json(
        ROOT / "rgkp/candidates" / relative
        / "generation/candidates.json"
    )

    routing = read_json(
        ROOT / "rgkp/candidates" / relative
        / "routing/routing.json"
    )

    questions = read_json(
        ROOT / "rgkp/probes" / relative
        / "questions/questions.json"
    )

    baseline = read_json(
        ROOT / "rgkp/probes" / relative
        / "baseline/baseline.json"
    )

    config = yaml.safe_load(
        (ROOT / "configs/experiment.yaml").read_text()
    )

    models = yaml.safe_load(
        (ROOT / "configs/models.yaml").read_text()
    )

    model = models["models"]["target_agent"]["model_id"]

    repetitions = config[
        "intervention"
    ]["repetitions_per_candidate"]

    expected_count = config[
        "candidate_generation"
    ]["candidates_per_target"]

    if repetitions < 1 or repetitions % 2 == 0:
        raise ValueError("Expected odd repetition count")

    if repetitions != baseline["repetitions"]:
        raise ValueError("Baseline/intervention repetition mismatch")

    if baseline["model"] != model:
        raise ValueError("Baseline model mismatch")

    question_map = {
        q["target_id"]: q
        for q in questions["diagnostic_questions"]
    }

    baseline_map = {
        item["target_id"]: item
        for item in baseline["baseline_results"]
    }

    eligible = {
        item["target_id"]
        for item in routing["routing_results"]
        if item["route"] == "candidate_generation"
    }

    candidate_map = {}

    for candidate in candidate_data["candidates"]:

        target_id = candidate["target_id"]

        if target_id not in eligible:
            raise ValueError("Ineligible candidate target")

        candidate_map.setdefault(
            target_id, []
        ).append(candidate)

    if set(candidate_map) != eligible:
        raise ValueError("Missing eligible candidates")

    output_dir = (
        ROOT / "rgkp/interventions" / relative
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    print("Model:", model, flush=True)
    print("Eligible targets:", len(eligible), flush=True)
    print("Repetitions:", repetitions, flush=True)

    all_results = []
    selections = []

    for target_id in sorted(eligible):

        question = question_map[target_id]

        if baseline_map[target_id]["baseline_state"] != 0:
            raise ValueError("Baseline must be incorrect")

        if question["status"] != "ready":
            raise ValueError("Question is not ready")

        if (
            baseline_map[target_id]["reference_answer"]
            != question["correct_answer"]
        ):
            raise ValueError("Reference answer mismatch")

        candidates = candidate_map[target_id]

        if len(candidates) != expected_count:
            raise ValueError("Incorrect candidate count")

        if len({
            c["candidate_id"] for c in candidates
        }) != expected_count:
            raise ValueError("Duplicate candidate ID")

        target_dir = output_dir / target_id
        target_dir.mkdir(exist_ok=True)

        results = []

        for candidate in candidates:

            result = evaluate_candidate(
                candidate,
                question,
                model,
                repetitions,
                target_dir
            )

            results.append(result)
            all_results.append(result)

        effective = [
            result for result in results
            if result["delta"] == 1
        ]

        if effective:

            selected = min(
                effective,
                key=lambda item: (
                    item["candidate_tokens"],
                    item["candidate_id"]
                )
            )

            selection = {
                "target_id": target_id,
                "status": "selected",
                "selected_candidate": selected
            }

        else:

            selection = {
                "target_id": target_id,
                "status": "unresolved",
                "selected_candidate": None
            }

        selections.append(selection)

        save_json(
            target_dir / "selection.json",
            selection
        )

        print(
            f"{target_id}: {selection['status']}",
            flush=True
        )

    save_json(
        output_dir / "interventions.json",
        {
            "model": model,
            "repetitions": repetitions,
            "intervention_results": all_results
        }
    )

    save_json(
        output_dir / "selections.json",
        {
            "selections": selections
        }
    )

    print("\n===== Intervention Summary =====")
    print("Eligible targets:", len(eligible))
    print("Evaluated candidates:", len(all_results))

    print("Effective candidates:", sum(
        item["delta"] == 1
        for item in all_results
    ))

    print("Selected candidates:", sum(
        item["status"] == "selected"
        for item in selections
    ))

    if not eligible:
        print("No eligible targets. No API call.")

    print("Results saved to:", output_dir)


if __name__ == "__main__":
    main()
