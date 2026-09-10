"""Day 1: instrument three Mistral extraction calls."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from promptlab.config import PROJECT_ROOT, Settings
from promptlab.usage import CallRecord, append_record, compute_cost

CASE_IDS = ("E12", "E07", "E11")
PROMPT_ID = "baseline"
PROMPT_VERSION = "v0"
NORMAL_NUM_PREDICT = 256


def load_cases(path: Path) -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row: dict[str, Any] = json.loads(line)
        cases[str(row["id"])] = row
    return cases


def load_prompt(path: Path, document_text: str) -> str:
    return path.read_text(encoding="utf-8").replace("{document_text}", document_text)


def call_ollama(
    *,
    base_url: str,
    model_id: str,
    prompt: str,
    temperature: float,
    num_predict: int,
) -> tuple[dict[str, Any], int]:
    started = time.perf_counter()
    response = httpx.post(
        f"{base_url}/api/generate",
        json={
            "model": model_id,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": num_predict},
        },
        timeout=180.0,
    )
    response.raise_for_status()
    latency_ms = int((time.perf_counter() - started) * 1000)
    payload: dict[str, Any] = response.json()
    return payload, latency_ms


def record_from_payload(
    *,
    payload: dict[str, Any],
    run_id: str,
    model_id: str,
    case_id: str,
    temperature: float,
    num_predict: int,
    latency_ms: int,
    error_type: str | None,
) -> CallRecord:
    input_tokens = int(payload.get("prompt_eval_count") or 0)
    output_tokens = int(payload.get("eval_count") or 0)
    stop_reason = payload.get("done_reason")
    response_text = payload.get("response")
    return CallRecord(
        record_id=str(uuid.uuid4()),
        run_id=run_id,
        timestamp=datetime.now(UTC),
        provider="ollama",
        model_id=model_id,
        task="extraction",
        case_id=case_id,
        prompt_id=PROMPT_ID,
        prompt_version=PROMPT_VERSION,
        attempt=1,
        temperature=temperature,
        max_output_tokens=num_predict,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=None,
        latency_ms=latency_ms,
        cost_usd=compute_cost(model_id, input_tokens, output_tokens),
        stop_reason=str(stop_reason) if stop_reason is not None else None,
        error_type=error_type,
        response_text=str(response_text) if response_text is not None else None,
    )


def main() -> None:
    settings = Settings.from_env()
    model = settings.models["mistral"]
    temperature = 0.0
    cases = load_cases(PROJECT_ROOT / "cases" / "extraction.jsonl")
    prompt_path = PROJECT_ROOT / "src" / "prompts" / "baseline.v0.md"
    run_id = str(uuid.uuid4())
    print(f"run_id={run_id}")

    for case_id in CASE_IDS:
        prompt = load_prompt(prompt_path, str(cases[case_id]["source"]))
        payload, latency_ms = call_ollama(
            base_url=settings.ollama_base_url,
            model_id=model.model_id,
            prompt=prompt,
            temperature=temperature,
            num_predict=NORMAL_NUM_PREDICT,
        )
        record = record_from_payload(
            payload=payload,
            run_id=run_id,
            model_id=model.model_id,
            case_id=case_id,
            temperature=temperature,
            num_predict=NORMAL_NUM_PREDICT,
            latency_ms=latency_ms,
            error_type=None,
        )
        append_record(record, run_id)
        print(
            case_id,
            "input=", record.input_tokens,
            "output=", record.output_tokens,
            "latency_ms=", record.latency_ms,
            "stop=", record.stop_reason,
        )


if __name__ == "__main__":
    main()