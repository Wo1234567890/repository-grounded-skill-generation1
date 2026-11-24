import json
import re


def parse_json_object(raw):
    raw = raw.strip()

    if not raw:
        raise ValueError("Empty model response")

    try:
        result = json.loads(raw)

    except json.JSONDecodeError:
        blocks = re.findall(
            r"^[ \t]*```(?:json)?[ \t]*\r?\n"
            r"(.*?)"
            r"^[ \t]*```[ \t]*$",
            raw,
            flags=re.DOTALL | re.MULTILINE | re.IGNORECASE
        )

        if len(blocks) == 1:
            result = json.loads(blocks[0])

        elif not blocks:
            # Allow explanatory text before a single JSON object.
            starts = list(re.finditer(r"(?m)^\{", raw))

            if len(starts) != 1:
                raise ValueError("No unambiguous JSON object")

            result = json.loads(
                raw[starts[0].start():]
            )

        else:
            raise ValueError("Multiple JSON blocks")

    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")

    return result
