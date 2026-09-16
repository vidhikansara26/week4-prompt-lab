"""Day 4: compare triage.v1 vs triage.v2 on one local model."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from promptlab.adapters.base import CompletionRequest, CompletionResult
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import PROJECT_ROOT, Settings
from promptlab.prompts import load, render_user
from promptlab.records import OutputRecord, append_record
from promptlab.schemas import (
    StrictModel,
    TriageOutput,
    TriageOutputWithAnalysis,
    schema_description,
)
from promptlab.scoring import score_case
from promptlab.structured import complete_structured

MAX_OUTPUT_TOKENS = 512
LOGICAL_MODEL = "mistral"
PROMPT_ID = "triage"
VERSIONS: tuple[tuple[str, type[StrictModel]], ...] = (
    ("v1", TriageOutput),
    ("v2", TriageOutputWithAnalysis),
)


class RecordingAdapter:
    """Count complete() calls (primary + schema repairs) and keep token/latency."""

    def __init__(self, inner: OllamaAdapter) -> None:
        self._inner = inner
        self.provider = inner.provider
        self.model_id = inner.model_id
        self.calls = 0
        self.results: list[CompletionResult] = []

    def complete(self, request: CompletionRequest, run_id: str) -> CompletionResult:
        self.calls += 1
        result = self._inner.complete(request, run_id)
        self.results.append(result)
        return result


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _usage(adapter: RecordingAdapter) -> tuple[int, int]:
    output_tokens = 0
    latency_ms = 0
    for result in adapter.results:
        for record in result.records:
            output_tokens += record.output_tokens
            latency_ms += record.latency_ms
    return output_tokens, latency_ms


def main() -> None:
    settings = Settings.from_env()
    model = settings.models[LOGICAL_MODEL]
    run_id = str(uuid.uuid4())
    adapter = OllamaAdapter(model_id=model.model_id)

    cases = load_jsonl(PROJECT_ROOT / "cases" / "triage.jsonl")
    gold_rows = load_jsonl(PROJECT_ROOT / "cases" / "gold" / "triage.jsonl")
    gold = {str(row["id"]): row for row in gold_rows}

    run_path = PROJECT_ROOT / "docs" / "day4-run.jsonl"
    score_path = PROJECT_ROOT / "docs" / "day4-scores.jsonl"
    run_path.write_text("", encoding="utf-8")
    score_path.write_text("", encoding="utf-8")

    print(f"run_id={run_id} model={model.model_id}")

    for version, schema in VERSIONS:
        template = load(PROMPT_ID, version)
        for case in cases:
            case_id = str(case["id"])
            recorder = RecordingAdapter(adapter)
            request = CompletionRequest(
                task="triage",
                case_id=case_id,
                prompt_id=PROMPT_ID,
                prompt_version=version,
                system=template.system,
                user_content=render_user(
                    template,
                    variables={"schema_description": schema_description(schema)},
                    untrusted=str(case["source"]),
                ),
                temperature=0.0,
                max_output_tokens=MAX_OUTPUT_TOKENS,
            )
            output: dict[str, Any] | None = None
            error: str | None = None
            try:
                obj = complete_structured(
                    recorder,
                    request,
                    schema,
                    run_id,
                    max_repairs=settings.max_schema_repairs,
                )
                output = obj.model_dump()
            except (RuntimeError, ValidationError, json.JSONDecodeError, ValueError) as exc:
                error = str(exc)

            output_tokens, latency_ms = _usage(recorder)
            append_record(
                run_path,
                OutputRecord(
                    run_id=run_id,
                    task="triage",
                    case_id=case_id,
                    model_name=LOGICAL_MODEL,
                    model_id=model.model_id,
                    prompt_version=version,
                    succeeded=output is not None,
                    repairs=max(recorder.calls - 1, 0),
                    output=output,
                    error=error,
                ),
            )
            expected = gold[case_id]
            for score in score_case(
                run_id=run_id,
                case_id=case_id,
                model_name=LOGICAL_MODEL,
                prompt_version=version,
                output=output,
                expected_queue=str(expected["expected_queue"]),
                expected_escalation=bool(expected["expected_escalation"]),
            ):
                if score.metric == "queue_accuracy":
                    score = score.model_copy(
                        update={
                            "detail": (
                                f"queue={score.detail}; "
                                f"output_tokens={output_tokens}; "
                                f"latency_ms={latency_ms}"
                            )
                        }
                    )
                append_record(score_path, score)

            print(version, case_id, "ok" if output else "FAIL", error or "")


if __name__ == "__main__":
    main()