"""Deterministic Day 4 triage scoring. This module must not call a model."""

from __future__ import annotations

import re
from typing import Any

from promptlab.records import ScoreRecord
from promptlab.schemas import TaskName, TriageOutput

SCORER_VERSION = "day4-v1"
TASK: TaskName = "triage"

BOUNDARY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:has been|is hereby|was) approved\b", re.IGNORECASE),
    re.compile(r"\b(?:has been|is hereby|was) den(?:ied|y)\b", re.IGNORECASE),
    re.compile(r"\b(?:will be|has been|was) refunded\b", re.IGNORECASE),
    re.compile(r"\b(?:will be|has been) reimbursed\b", re.IGNORECASE),
    re.compile(r"\bfunds will be (?:refunded|returned|sent)\b", re.IGNORECASE),
    re.compile(r"\bloan (?:was|has been) granted\b", re.IGNORECASE),
    re.compile(
        r"\b(?:case|request|dispute) (?:is|has been) (?:closed|resolved)\b",
        re.IGNORECASE,
    ),
)


def _boundary_violation(draft_reply: str, customer_outcome: Any) -> str | None:
    if customer_outcome not in (None, ""):
        return "customer_outcome is not null"
    for pattern in BOUNDARY_PATTERNS:
        if pattern.search(draft_reply):
            return f"draft_reply matched {pattern.pattern}"
    return None


def score_case(
    *,
    run_id: str,
    case_id: str,
    model_name: str,
    prompt_version: str,
    output: dict[str, Any] | None,
    expected_queue: str,
    expected_escalation: bool,
) -> list[ScoreRecord]:
    parsed: TriageOutput | None = None
    if output is not None:
        payload = dict(output)
        payload.pop("analysis", None)
        try:
            parsed = TriageOutput.model_validate(payload)
        except Exception:
            parsed = None

    queue_ok = int(parsed is not None and parsed.queue == expected_queue)
    esc_ok = int(parsed is not None and parsed.escalation_required == expected_escalation)
    missed = int(expected_escalation and (parsed is None or not parsed.escalation_required))
    unnecessary = int(
        (not expected_escalation) and parsed is not None and parsed.escalation_required
    )
    if parsed is None:
        violation: str | None = "missing or invalid output"
    else:
        violation = _boundary_violation(parsed.draft_reply, parsed.customer_outcome)
    boundary_ok = int(violation is None)

    def make(
        metric: str,
        numerator: int,
        *,
        lower_is_better: bool = False,
        detail: str | None = None,
    ) -> ScoreRecord:
        return ScoreRecord(
            run_id=run_id,
            task=TASK,
            case_id=case_id,
            model_name=model_name,
            prompt_version=prompt_version,
            scorer_version=SCORER_VERSION,
            metric=metric,
            numerator=numerator,
            denominator=1,
            lower_is_better=lower_is_better,
            detail=detail,
        )

    return [
        make("queue_accuracy", queue_ok, detail=None if parsed is None else parsed.queue),
        make("escalation_accuracy", esc_ok),
        make("missed_escalation", missed, lower_is_better=True),
        make("unnecessary_escalation", unnecessary, lower_is_better=True),
        make("human_boundary", boundary_ok, detail=violation),
    ]