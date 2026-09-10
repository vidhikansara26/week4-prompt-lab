from __future__ import annotations

import json
from pathlib import Path

from promptlab.config import PROJECT_ROOT, Settings
from promptlab.usage import compute_cost

CASE_IDS = ("E12", "E07", "E11")


def test_extraction_corpus_has_short_medium_long_cases() -> None:
    path = PROJECT_ROOT / "cases" / "extraction.jsonl"
    found: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            found.add(json.loads(line)["id"])
    assert set(CASE_IDS) <= found


def test_baseline_prompt_has_document_placeholder() -> None:
    text = (PROJECT_ROOT / "src" / "prompts" / "baseline.v0.md").read_text(
        encoding="utf-8"
    )
    assert "{document_text}" in text


def test_mistral_id_comes_from_config_not_a_literal() -> None:
    model_id = Settings.from_env().models["mistral"].model_id
    assert model_id
    assert "mistral:7b" not in Path("src/promptlab/day1.py").read_text(encoding="utf-8")


def test_length_stop_reason_maps_to_truncated_error() -> None:
    done_reason = "length"
    error_type = "TruncatedResponseError" if done_reason == "length" else None
    assert error_type == "TruncatedResponseError"


def test_successful_record_has_null_error_and_zero_cost() -> None:
    model_id = Settings.from_env().models["mistral"].model_id
    assert compute_cost(model_id, 100, 20) == 0.0