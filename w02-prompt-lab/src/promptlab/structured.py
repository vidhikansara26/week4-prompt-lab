from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

from promptlab.adapters.base import CompletionRequest, ModelAdapter

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _parse_json(text: str) -> object:
    stripped = text.strip()
    match = _FENCE.search(stripped)
    if match:
        stripped = match.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end > start:
            return json.loads(stripped[start : end + 1])
        raise


def complete_structured[T: BaseModel](
    adapter: ModelAdapter,
    request: CompletionRequest,
    schema: type[T],
    run_id: str,
    max_repairs: int = 1,
) -> T:
    """Return a schema-validated completion with a bounded semantic repair loop.

    Transport retry remains inside the adapter.
    Schema/content repair belongs here.

    On validation failure, send the validation error text back to the model and
    instruct it to correct only what the error concerns. Do not perform more
    than max_repairs semantic repair attempts.
    """

    result = adapter.complete(request, run_id)
    if not result.succeeded or result.text is None:
        raise RuntimeError(result.error_type or "empty model response")

    last_text = result.text
    last_error: Exception | None = None
    try:
        return schema.model_validate(_parse_json(last_text))
    except (ValidationError, json.JSONDecodeError, ValueError) as exc:
        last_error = exc

    for _ in range(max_repairs):
        repair_request = request.model_copy(
            update={
                "user_content": (
                    f"{request.user_content}\n\n"
                    "Your previous response failed validation with the following "
                    "error. Return a corrected filled JSON instance. "
                    "Do not change any field the error does not concern. "
                    "Do not echo JSON Schema keys such as type, title, or $defs.\n"
                    f"<error>\n{last_error}\n</error>\n\n"
                    f"Previous output:\n{last_text}"
                )
            }
        )
        repaired = adapter.complete(repair_request, run_id)
        if not repaired.succeeded or repaired.text is None:
            raise RuntimeError(
                repaired.error_type or "empty repair response"
            ) from last_error
        last_text = repaired.text
        try:
            return schema.model_validate(_parse_json(last_text))
        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            last_error = exc

    assert last_error is not None
    raise last_error
