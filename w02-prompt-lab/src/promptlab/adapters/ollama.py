from __future__ import annotations

import random
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from promptlab.adapters.base import CompletionRequest, CompletionResult
from promptlab.config import ModelConfig, Settings
from promptlab.errors import (
    PermanentProviderError,
    TransientProviderError,
    TruncatedResponseError,
    UnknownModelError,
)
from promptlab.usage import CallRecord, append_record, compute_cost

MAX_ATTEMPTS = 3
TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}


class OllamaAdapter:
    provider = "ollama"

    def __init__(self, model_id: str) -> None:
        settings = Settings.from_env()
        config = next(
            (item for item in settings.models.values() if item.model_id == model_id),
            None,
        )
        if config is None:
            raise UnknownModelError(f"Unknown model identifier: {model_id}")
        self.model_id = model_id
        self._config: ModelConfig = config
        self._base_url = settings.ollama_base_url

    def complete(self, request: CompletionRequest, run_id: str) -> CompletionResult:
        records: list[CallRecord] = []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            record = self._one_attempt(request, run_id, attempt)
            records.append(record)
            append_record(record, run_id)

            if record.error_type is None:
                return CompletionResult(
                    succeeded=True,
                    text=record.response_text,
                    error_type=None,
                    records=records,
                )
            if record.error_type != TransientProviderError.__name__ or attempt == MAX_ATTEMPTS:
                return CompletionResult(
                    succeeded=False,
                    text=None,
                    error_type=record.error_type,
                    records=records,
                )
            time.sleep((2 ** (attempt - 1)) + random.random())

        return CompletionResult(
            succeeded=False,
            text=None,
            error_type=TransientProviderError.__name__,
            records=records,
        )

    def _one_attempt(
        self,
        request: CompletionRequest,
        run_id: str,
        attempt: int,
    ) -> CallRecord:
        started = time.perf_counter()
        payload: dict[str, Any] = {}
        error_type: str | None = None
        body: dict[str, Any] = {
            "model": self.model_id,
            "prompt": f"{request.system}\n\n{request.user_content}",
            "stream": False,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_output_tokens,
            },
        }
        if self._config.think is not None:
            body["think"] = self._config.think
        try:
            response = httpx.post(
                f"{self._base_url}/api/generate",
                json=body,
                timeout=180.0,
            )
            try:
                parsed = response.json()
            except ValueError:
                parsed = {}
            if isinstance(parsed, dict):
                payload = parsed
            error_type = _classify_status(response.status_code, payload)
        except (httpx.TimeoutException, httpx.RequestError):
            error_type = TransientProviderError.__name__

        latency_ms = int((time.perf_counter() - started) * 1000)
        return _to_call_record(
            payload=payload,
            request=request,
            run_id=run_id,
            model_id=self.model_id,
            attempt=attempt,
            latency_ms=latency_ms,
            error_type=error_type,
        )


def _classify_status(status_code: int, payload: dict[str, Any]) -> str | None:
    if status_code in TRANSIENT_STATUS_CODES or status_code >= 500:
        return TransientProviderError.__name__
    if status_code >= 400:
        return PermanentProviderError.__name__
    if payload.get("done_reason") == "length":
        return TruncatedResponseError.__name__
    return None


def _response_text(payload: dict[str, Any]) -> str | None:
    response = payload.get("response")
    if isinstance(response, str):
        return response
    message = payload.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
    return None


def _to_call_record(
    *,
    payload: dict[str, Any],
    request: CompletionRequest,
    run_id: str,
    model_id: str,
    attempt: int,
    latency_ms: int,
    error_type: str | None,
) -> CallRecord:
    input_tokens = int(payload.get("prompt_eval_count") or 0)
    output_tokens = int(payload.get("eval_count") or 0)
    stop_reason = payload.get("done_reason")
    return CallRecord(
        record_id=str(uuid.uuid4()),
        run_id=run_id,
        timestamp=datetime.now(UTC),
        provider="ollama",
        model_id=model_id,
        task=request.task,
        case_id=request.case_id,
        prompt_id=request.prompt_id,
        prompt_version=request.prompt_version,
        attempt=attempt,
        temperature=request.temperature,
        max_output_tokens=request.max_output_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=None,
        latency_ms=latency_ms,
        cost_usd=compute_cost(model_id, input_tokens, output_tokens),
        stop_reason=str(stop_reason) if stop_reason is not None else None,
        error_type=error_type,
        response_text=_response_text(payload),
    )
