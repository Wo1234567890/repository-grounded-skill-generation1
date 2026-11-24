import argparse
import json
import time
from pathlib import Path

import yaml
from anthropic import Anthropic
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

load_dotenv(ROOT / ".env")


def read_json(path):
    return json.loads(path.read_text())


def save_json(path, data):
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def probe_question(question, model, candidate=None):

    system_prompt = (
        ROOT / "configs/prompts/baseline_probe.txt"
    ).read_text()

    # Only send the question and answer options.
    payload = {
        "question": question["question"],
        "options": question["options"]
    }

    if candidate is not None:
        payload["candidate_knowledge"] = candidate

    client = Anthropic(
        timeout=120,
        max_retries=0
    )

    start = time.monotonic()

    response = client.messages.create(
        model=model,
        max_tokens=256,
        system=system_prompt,
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
    ).strip()

    answer = raw.upper()

    valid = (
        answer in ["A", "B", "C", "D"]
        and response.stop_reason == "end_turn"
        and response.model == model
    )

    return {
        "input": payload,
        "model": response.model,
        "raw_response": raw,
        "answer": answer if valid else None,
        "valid": valid,
        "stop_reason": response.stop_reason,
        "wall_time_sec": time.monotonic() - start,
        "usage": response.usage.model_dump()
    }


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("--batch", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--run", required=True)

    args = parser.parse_args()

    relative = (
        Path(args.batch) / args.task / args.run
    )

    questions = read_json(
        ROOT / "rgkp/probes" / relative
        / "questions/questions.json"
    )

    config = yaml.safe_load(
        (ROOT / "configs/experiment.yaml").read_text()
    )

    models = yaml.safe_load(
        (ROOT / "configs/models.yaml").read_text()
    )

    model = models["models"]["target_agent"]["model_id"]

    repetitions = config[
        "diagnostic_probing"
    ]["repetitions"]

    if repetitions < 1 or repetitions % 2 == 0:
        raise ValueError("Expected odd repetition count")

    output_dir = (
        ROOT / "rgkp/probes" / relative / "baseline"
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    results = []

    print("Model:", model, flush=True)
    print("Repetitions:", repetitions, flush=True)

    for question in questions["diagnostic_questions"]:

        target_id = question["target_id"]

        if question["status"] != "ready":

            results.append({
                "target_id": target_id,
                "status": "skipped",
                "baseline_state": None
            })

            continue

        target_dir = output_dir / target_id
        target_dir.mkdir(exist_ok=True)

        expected_input = {
            "question": question["question"],
            "options": question["options"]
        }

        records = []

        print(f"\nTarget: {target_id}", flush=True)

        for index in range(1, repetitions + 1):

            path = target_dir / f"probe_{index}.json"

            if path.exists():

                record = read_json(path)

                if (
                    record["input"] != expected_input
                    or record["model"] != model
                ):
                    raise ValueError(
                        "Saved probe configuration mismatch"
                    )

                print(
                    f"Probe {index}: reusing saved result",
                    flush=True
                )

            else:

                record = probe_question(
                    question,
                    model
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

            baseline_state = None
            status = "unresolved"

        else:

            baseline_state = int(
                correct_count > repetitions / 2
            )

            status = "completed"

        result = {
            "target_id": target_id,
            "question_id": question["question_id"],
            "reference_answer": question["correct_answer"],
            "answers": [
                r["answer"] for r in records
            ],
            "repetitions": repetitions,
            "valid_count": valid_count,
            "correct_count": correct_count,
            "baseline_state": baseline_state,
            "status": status
        }

        results.append(result)

        save_json(target_dir / "summary.json", result)

        print(
            f"Baseline state: {baseline_state}",
            flush=True
        )

    output = {
        "model": model,
        "repetitions": repetitions,
        "baseline_results": results
    }

    save_json(
        output_dir / "baseline.json",
        output
    )

    print("\n===== Baseline Probing Summary =====")

    print("Total targets:", len(results))

    print("Baseline correct:", sum(
        r["baseline_state"] == 1
        for r in results
    ))

    print("Baseline incorrect:", sum(
        r["baseline_state"] == 0
        for r in results
    ))

    print("Unresolved or skipped:", sum(
        r["baseline_state"] is None
        for r in results
    ))

    print("Results saved to:", output_dir)


if __name__ == "__main__":
    main()
