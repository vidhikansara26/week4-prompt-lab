"""Day 3: structured summarization and extraction with one bounded repair."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from promptlab.adapters.base import CompletionRequest, CompletionResult
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import PROJECT_ROOT, Settings
from promptlab.schemas import (
    PolicyExtraction,
    StrictModel,
    SummarizationOutput,
    TaskName,
    schema_description,
)
from promptlab.structured import complete_structured

MAX_OUTPUT_TOKENS = 512
LEAKAGE_NEEDLES = (
    "Alder Quay",
    "Redhaven",
    "East Kestrel",
)


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row: dict[str, Any] = json.loads(line)
        cases.append(row)
    return cases


def build_user(template: str, source: str, schema: type[StrictModel]) -> str:
    return template.replace("{document_text}", source).replace(
        "{schema_description}",
        schema_description(schema),
    )


class CallCounter:
    def __init__(self, inner: OllamaAdapter) -> None:
        self._inner = inner
        self.provider = inner.provider
        self.model_id = inner.model_id
        self.calls = 0

    def complete(self, request: CompletionRequest, run_id: str) -> CompletionResult:
        self.calls += 1
        return self._inner.complete(request, run_id)


def count_leakage(rows: list[dict[str, Any]]) -> int:
    hits = 0
    for row in rows:
        if row["task"] != "extraction" or row.get("output") is None:
            continue
        blob = json.dumps(row["output"])
        if any(needle in blob for needle in LEAKAGE_NEEDLES):
            hits += 1
    return hits


def count_citation_failures(
    rows: list[dict[str, Any]],
    sources: dict[str, str],
) -> int:
    failures = 0
    for row in rows:
        if row.get("output") is None:
            continue
        if row["task"] == "extraction":
            obj: PolicyExtraction | SummarizationOutput = (
                PolicyExtraction.model_validate(row["output"])
            )
        else:
            obj = SummarizationOutput.model_validate(row["output"])
        source = sources[row["case_id"]]
        for field in obj.evidence_fields().values():
            if field.status != "present":
                continue
            citation = field.citation or ""
            if not citation or citation not in source:
                failures += 1
    return failures


def main() -> None:
    settings = Settings.from_env()
    run_id = str(uuid.uuid4())
    adapter = OllamaAdapter(model_id=settings.models["mistral"].model_id)
    out_path = PROJECT_ROOT / "docs" / "day3-run.jsonl"
    out_path.write_text("", encoding="utf-8")
    print(f"run_id={run_id}")

    jobs: list[tuple[Path, Path, type[StrictModel], str, str, TaskName]] = [
        (
            PROJECT_ROOT / "cases" / "summarization.jsonl",
            PROJECT_ROOT / "src" / "prompts" / "summarize.v1.md",
            SummarizationOutput,
            "summarize",
            "v1",
            "summarization",
        ),
        (
            PROJECT_ROOT / "cases" / "extraction.jsonl",
            PROJECT_ROOT / "src" / "prompts" / "extract.v2.md",
            PolicyExtraction,
            "extract",
            "v2",
            "extraction",
        ),
    ]

    rows: list[dict[str, Any]] = []
    sources: dict[str, str] = {}

    for cases_path, prompt_path, schema, prompt_id, version, task in jobs:
        template = prompt_path.read_text(encoding="utf-8")
        repairs = 0
        for case in load_cases(cases_path):
            case_id = str(case["id"])
            sources[case_id] = str(case["source"])
            counter = CallCounter(adapter)
            request = CompletionRequest(
                task=task,
                case_id=case_id,
                prompt_id=prompt_id,
                prompt_version=version,
                system=(
                    "Return only a JSON object with no markdown and no prose. "
                    "document_status must be valid, contradictory, superseded, or "
                    "unsupported. Use superseded if the document says it was "
                    "replaced. Use contradictory if two sections conflict and no "
                    "precedence rule is given; do not pick a winner. Use "
                    "unsupported for agendas, workshops, newsletters, meeting "
                    "notes, and release notes. Never use non-valid. "
                    "Citations must be full section headings, not a bare number. "
                    "Every evidence field must include value, status, and citation. "
                    "If a field is absent, emit "
                    '{"value": null, "status": "absent", "citation": null}.'
                ),
                user_content=build_user(template, str(case["source"]), schema),
                temperature=0.0,
                max_output_tokens=MAX_OUTPUT_TOKENS,
            )
            try:
                obj = complete_structured(
                    counter, request, schema, run_id, max_repairs=1
                )
            except (RuntimeError, ValidationError, json.JSONDecodeError) as exc:
                row = {
                    "run_id": run_id,
                    "task": task,
                    "case_id": case_id,
                    "prompt_id": prompt_id,
                    "prompt_version": version,
                    "repairs": int(counter.calls > 1),
                    "output": None,
                    "error": str(exc),
                }
                rows.append(row)
                repairs += int(counter.calls > 1)
                with out_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row) + "\n")
                print(task, case_id, "FAILED")
                continue
            used_repair = int(counter.calls > 1)
            repairs += used_repair
            row = {
                "run_id": run_id,
                "task": task,
                "case_id": case_id,
                "prompt_id": prompt_id,
                "prompt_version": version,
                "repairs": used_repair,
                "output": obj.model_dump(),
            }
            rows.append(row)
            with out_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
        print(task, "repair rate", f"{repairs}/12")

    print("example leakage count", count_leakage(rows))
    print("citation-existence failure count", count_citation_failures(rows, sources))


if __name__ == "__main__":
    main()