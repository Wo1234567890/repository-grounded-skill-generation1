import argparse
import json
import time
from pathlib import Path

import yaml
from anthropic import Anthropic
from dotenv import load_dotenv

from utils.json_parser import parse_json_object
from rgkp.fallback_skill import construct_fallback

ROOT = Path(__file__).resolve().parents[1]

load_dotenv(ROOT / ".env")


def read_json(path):
    return json.loads(path.read_text())


def save_json(path, data):
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def collect_units(selections, guidance, config):

    units = []

    if config["include_validated_knowledge"]:

        for item in selections["selections"]:

            if item["status"] != "selected":
                continue

            candidate = item["selected_candidate"]

            if (
                candidate["baseline_state"] != 0
                or candidate["delta"] != 1
                or candidate["intervention_state"] != 1
            ):
                raise ValueError("Unvalidated candidate")

            units.append({
                "source_id": candidate["candidate_id"],
                "type": "validated_knowledge",
                "content": candidate["knowledge"]
            })

    if config["include_execution_guidance"]:

        for item in guidance["application_results"]:

            if item["application_state"] != "not_applied":
                continue

            units.append({
                "source_id": item["guidance_id"],
                "type": "execution_guidance",
                "content": item["execution_guidance"]
            })

    ids = [unit["source_id"] for unit in units]

    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate source IDs")

    return units


def recompose(units, model, output_dir):

    prompt = (
        ROOT / "configs/prompts/residual_construction.txt"
    ).read_text()

    request = {
        "model": model,
        "system_prompt": prompt,
        "payload": {"source_units": units}
    }

    input_path = output_dir / "input.json"
    raw_path = output_dir / "raw_response.txt"
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
                        request["payload"],
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


def validate_residual(result, units):

    source_map = {
        unit["source_id"]: unit
        for unit in units
    }

    covered = set()

    for index, item in enumerate(
        result["residual_units"], 1
    ):

        kind = item["type"]
        content = item["content"].strip()
        source_ids = item["source_ids"]

        if kind not in [
            "validated_knowledge",
            "execution_guidance"
        ]:
            raise ValueError("Invalid residual type")

        if not content or not source_ids:
            raise ValueError("Empty or unsupported unit")

        for source_id in source_ids:

            if source_id not in source_map:
                raise ValueError("Unknown source ID")

            if source_map[source_id]["type"] != kind:
                raise ValueError("Mixed source types")

            covered.add(source_id)

        item["residual_id"] = f"R{index}"

    if covered != set(source_map):
        raise ValueError("Missing source units")

    return result


def render_skill(result):

    lines = [
        "---",
        "name: rgkp-residual",
        "description: Repository-grounded guidance for the target task.",
        "---",
        "",
        "# Residual Skill",
        ""
    ]

    sections = [
        ("validated_knowledge", "Validated Knowledge"),
        ("execution_guidance", "Execution Guidance")
    ]

    for kind, title in sections:

        selected = [
            item for item in result["residual_units"]
            if item["type"] == kind
        ]

        if not selected:
            continue

        lines.extend([f"## {title}", ""])

        for index, item in enumerate(selected, 1):

            lines.extend([
                f"### {index}",
                "",
                item["content"].strip(),
                ""
            ])

    return "\n".join(lines).strip() + "\n"


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("--batch", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--run", required=True)

    args = parser.parse_args()

    relative = (
        Path(args.batch) / args.task / args.run
    )

    selections = read_json(
        ROOT / "rgkp/interventions"
        / relative / "selections.json"
    )

    output_dir = (
        ROOT / "rgkp/residual_skills" / relative
    )

    guidance = read_json(
        output_dir / "guidance/application_check.json"
    )

    config = yaml.safe_load(
        (ROOT / "configs/experiment.yaml").read_text()
    )["residual_skill"]

    models = yaml.safe_load(
        (ROOT / "configs/models.yaml").read_text()
    )

    model = models["models"]["auxiliary_model"]["model_id"]

    if (output_dir / "manifest.json").exists():
        raise SystemExit("Residual results already exist.")

    recompose_dir = output_dir / "recomposition"
    recompose_dir.mkdir(parents=True, exist_ok=True)

    units = collect_units(
        selections, guidance, config
    )

    knowledge_count = sum(
        u["type"] == "validated_knowledge"
        for u in units
    )

    guidance_count = sum(
        u["type"] == "execution_guidance"
        for u in units
    )

    print("Model:", model, flush=True)
    print("Validated knowledge units:", knowledge_count)
    print("Execution guidance units:", guidance_count)

    save_json(
        recompose_dir / "source_units.json",
        {"source_units": units}
    )

    skill_path = output_dir / "SKILL.md"


    if not units:

        print("No residual units. Generating fallback skill.")

        skill, mode, fallback_count = construct_fallback(
            relative, model
        )

        skill_path.write_text(
            skill, encoding="utf-8"
        )

        save_json(
            recompose_dir / "residual_units.json",
            {"residual_units": []}
        )

        save_json(
            output_dir / "manifest.json",
            {
                "status": "generated",
                "generation_mode": mode,
                "model": model,
                "validated_knowledge_units": 0,
                "execution_guidance_units": 0,
                "source_units": 0,
                "residual_units": 0,
                "fallback_units": fallback_count,
                "skill_file": str(skill_path.relative_to(ROOT)),
                "skill_characters": len(skill)
            }
        )

        print("Generation mode:", mode)
        print("Skill saved to:", skill_path)
        print(skill)

        return

    print("Recomposing residual skill...", flush=True)

    result = recompose(
        units, model, recompose_dir
    )

    result = validate_residual(result, units)

    save_json(
        recompose_dir / "residual_units.json",
        result
    )

    skill = render_skill(result)

    skill_path.write_text(
        skill,
        encoding="utf-8"
    )

    save_json(
        output_dir / "manifest.json",
        {
            "status": "generated",
            "generation_mode": "residual",
            "model": model,
            "validated_knowledge_units": knowledge_count,
            "execution_guidance_units": guidance_count,
            "source_units": len(units),
            "residual_units": len(result["residual_units"]),
            "skill_file": str(skill_path.relative_to(ROOT)),
            "skill_characters": len(skill)
        }
    )

    print("\n===== Residual Construction Summary =====")
    print("Input units:", len(units))
    print("Residual units:", len(result["residual_units"]))
    print("Skill characters:", len(skill))
    print("Skill saved to:", skill_path)

    print("\n===== Final Skill =====\n")
    print(skill)


if __name__ == "__main__":
    main()
