"""Day 2: run the baseline summarization prompt on both configured local models."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from promptlab.adapters.base import CompletionRequest
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import PROJECT_ROOT, Settings

PROMPT_ID = "baseline"
PROMPT_VERSION = "v0"
MAX_OUTPUT_TOKENS = 512
LOGICAL_MODELS = ("mistral", "qwen")


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row: dict[str, Any] = json.loads(line)
        cases.append(row)
    return cases


def main() -> None:
    settings = Settings.from_env()
    run_id = str(uuid.uuid4())
    print(f"run_id={run_id}")

    cases = load_cases(PROJECT_ROOT / "cases" / "summarization.jsonl")
    prompt_template = (PROJECT_ROOT / "src" / "prompts" / "baseline.v0.md").read_text(
        encoding="utf-8"
    )

    for logical_name in LOGICAL_MODELS:
        adapter = OllamaAdapter(model_id=settings.models[logical_name].model_id)
        for case in cases:
            user_content = prompt_template.replace("{document_text}", str(case["source"]))
            request = CompletionRequest(
                task="summarization",
                case_id=str(case["id"]),
                prompt_id=PROMPT_ID,
                prompt_version=PROMPT_VERSION,
                system="You are reviewing an internal procedure document.",
                user_content=user_content,
                temperature=settings.temperature,
                max_output_tokens=MAX_OUTPUT_TOKENS,
            )
            result = adapter.complete(request, run_id)
            last = result.records[-1]
            print(
                logical_name,
                case["id"],
                "succeeded=",
                result.succeeded,
                "error=",
                result.error_type,
                "attempts=",
                len(result.records),
                "in=",
                last.input_tokens,
                "out=",
                last.output_tokens,
                "ms=",
                last.latency_ms,
            )


if __name__ == "__main__":
    main()
