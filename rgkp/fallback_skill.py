import json
import time
from pathlib import Path

from anthropic import Anthropic, APIError

from rgkp.gap_discovery import load_trajectory
from utils.validated_generation import generate_validated

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    return json.loads(path.read_text())


def save_json(path, data):
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False)
    )


def construct_fallback(relative, model):

    output_dir = (
        ROOT / "rgkp/residual_skills"
        / relative / "fallback"
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    run_dir = ROOT / "trajectories/no_skill" / relative

    gaps = read_json(
        ROOT / "rgkp/gaps" / relative / "gaps.json"
    )

    evidence = read_json(
        ROOT / "rgkp/evidence" / relative / "evidence.json"
    )

    targets = read_json(
        ROOT / "rgkp/targets" / relative / "targets.json"
    )

    baseline = read_json(
        ROOT / "rgkp/probes" / relative
        / "baseline/baseline.json"
    )

    payload = {
        "task_description": (run_dir / "task.md").read_text(),
        "trajectory": load_trajectory(
            run_dir / "trajectory.jsonl"
        ),
        "execution_gaps": gaps["execution_gaps"],
        "environment_blockers": gaps["environment_blockers"],
        "repository_evidence": evidence["gap_evidence"],
        "knowledge_targets": targets["gap_results"],
        "baseline_probing": baseline
    }

    if len(json.dumps(payload)) > 300000:
        payload.pop("trajectory")
        payload["trajectory_omitted"] = True

    prompt = (
        ROOT / "configs/prompts/fallback_skill.txt"
    ).read_text()

    request = {
        "model": model,
        "system_prompt": prompt,
        "payload": payload
    }

    input_path = output_dir / "input.json"

    if input_path.exists():
        if read_json(input_path) != request:
            raise ValueError("Saved fallback input mismatch")
    else:
        save_json(input_path, request)

    valid_ids = {
        e["evidence_id"]
        for item in evidence["gap_evidence"]
        for e in item["evidence"]
    }

    def generate(feedback):

        start = time.monotonic()

        response = Anthropic(
            timeout=180,
            max_retries=0
        ).messages.create(
            model=model,
            max_tokens=2048,
            system=prompt,
            messages=[{
                "role": "user",
                "content": json.dumps({
                    **payload,
                    "validation_feedback": feedback
                }, ensure_ascii=False)
            }]
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

    def validator(result):

        items = result["guidance"]

        if not isinstance(items, list):
            raise ValueError("Guidance must be a list")

        if not 1 <= len(items) <= 3:
            raise ValueError("Return 1 to 3 guidance units")

        for item in items:

            if not item["content"].strip():
                raise ValueError("Empty fallback guidance")

            ids = item["evidence_ids"]

            if not isinstance(ids, list):
                raise ValueError("Invalid evidence IDs")

            if any(eid not in valid_ids for eid in ids):
                raise ValueError("Unknown evidence ID")

        return result

    try:

        result = generate_validated(
            generate=generate,
            validator=validator,
            output_dir=output_dir,
            max_attempts=3
        )

        mode = "fallback"

    except (ValueError, KeyError, TypeError, AttributeError, APIError) as error:

        print("Fallback generation failed:", error)

        save_json(output_dir / "fallback_error.json", {
            "type": type(error).__name__,
            "message": str(error)
        })

        mode = "task_checklist"

        result = {
            "guidance": [
                {
                    "content": (
                        "Identify the mandatory requirements "
                        "and requested file paths in the task. "
                        "Inspect relevant repository implementations "
                        "and tests before making changes."
                    ),
                    "evidence_ids": []
                },
                {
                    "content": (
                        "Implement the required changes while "
                        "preserving existing repository interfaces. "
                        "Check every mandatory requirement before "
                        "finishing."
                    ),
                    "evidence_ids": []
                },
                {
                    "content": (
                        "Run available repository-supported "
                        "verification commands. Report blocked "
                        "or unexecuted checks explicitly instead "
                        "of claiming unverified success."
                    ),
                    "evidence_ids": []
                }
            ]
        }

    save_json(output_dir / "guidance.json", result)

    lines = [
        "---",
        "name: rgkp-fallback",
        "description: Task-specific execution guidance.",
        "---",
        "",
        "# Execution Guidance",
        ""
    ]

    for index, item in enumerate(result["guidance"], 1):

        lines.extend([
            f"## {index}",
            "",
            item["content"].strip(),
            ""
        ])

    skill = "\n".join(lines).strip() + "\n"

    return skill, mode, len(result["guidance"])
