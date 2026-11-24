import json
from pathlib import Path

from utils.json_parser import parse_json_object


def save_json(path, data):
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False)
    )


def validate(raw, metrics, validator):
    if metrics.get("stop_reason") != "end_turn":
        raise ValueError("Incomplete model response")

    return validator(parse_json_object(raw))


def archive_attempt(output_dir, raw, metrics):
    history = output_dir / "attempts"
    history.mkdir(exist_ok=True)

    index = 1
    while (history / f"attempt_{index:03d}").exists():
        index += 1

    folder = history / f"attempt_{index:03d}"
    folder.mkdir()

    (folder / "raw_response.txt").write_text(raw)
    save_json(folder / "metrics.json", metrics)

    return folder


def generate_validated(
    generate,
    validator,
    output_dir,
    max_attempts=3
):
    raw_path = output_dir / "raw_response.txt"
    metrics_path = output_dir / "metrics.json"

    # Preserve the previous failed response.
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

    for attempt in range(1, max_attempts + 1):

        print(
            f"Generation attempt {attempt}/{max_attempts}",
            flush=True
        )

        raw, metrics = generate(feedback)

        folder = archive_attempt(
            output_dir, raw, metrics
        )

        (folder / "validation_feedback.txt").write_text(
            feedback
        )

        raw_path.write_text(raw)
        save_json(metrics_path, metrics)

        try:
            result = validate(raw, metrics, validator)

            save_json(folder / "validation.json", {
                "status": "valid"
            })

            return result

        except (ValueError, KeyError, TypeError, AttributeError) as error:
            feedback = str(error)

            save_json(folder / "validation.json", {
                "status": "invalid",
                "error": feedback
            })

            print("Validation failed:", feedback, flush=True)

            if attempt == max_attempts:
                raise
