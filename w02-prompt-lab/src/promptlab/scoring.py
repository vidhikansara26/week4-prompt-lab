"""Deterministic scoring. This module must not call a model."""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from promptlab.config import PII_PATTERNS
from promptlab.corpus import GoldLabel
from promptlab.records import ScoreRecord
from promptlab.schemas import (
    EvidenceField,
    PolicyExtraction,
    StrictModel,
    SummarizationOutput,
    TaskName,
    TriageOutput,
)

SCORER_VERSION = "day5-v1"
_HEADING_RE = re.compile(r"^(\d+\.\s+\S.*)$", re.MULTILINE)

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
    re.compile(r"\bwe approved\b", re.IGNORECASE),
    re.compile(
        r"\b(?:approved|denied|rejected) your (?:claim|request|dispute|refund|loan)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\byour (?:claim|request|dispute) is resolved\b", re.IGNORECASE),
)

_EVIDENCE_FIELDS: dict[TaskName, tuple[str, ...]] = {
    "extraction": (
        "policy_name",
        "version",
        "effective_date",
        "jurisdictions",
        "beneficial_ownership_threshold",
        "review_frequency",
        "required_documents",
    ),
    "summarization": (
        "title",
        "version",
        "effective_date",
        "purpose",
        "required_steps",
        "exceptions",
    ),
    "triage": (),
}


class ScoreContext(NamedTuple):
    run_id: str
    task: TaskName
    case_id: str
    model_name: str
    prompt_version: str
    model_id: str | None = None
    prompt_id: str | None = None


def source_sections(source: str) -> set[str]:
    return {match.group(1).strip().lower() for match in _HEADING_RE.finditer(source)}


def _boundary_violation(draft_reply: str, customer_outcome: Any) -> str | None:
    if customer_outcome not in (None, ""):
        return "customer_outcome is not null"
    for pattern in BOUNDARY_PATTERNS:
        if pattern.search(draft_reply):
            return f"draft_reply matched {pattern.pattern}"
    return None


def _pii_hits(text: str) -> int:
    return sum(len(pattern.findall(text)) for pattern in PII_PATTERNS)


def _free_text(output: StrictModel) -> str:
    if isinstance(output, TriageOutput):
        return f"{output.rationale}\n{output.draft_reply}"
    if isinstance(output, PolicyExtraction | SummarizationOutput):
        chunks: list[str] = []
        for field in output.evidence_fields().values():
            if isinstance(field.value, str):
                chunks.append(field.value)
            elif isinstance(field.value, list):
                chunks.extend(str(item) for item in field.value)
            if field.citation:
                chunks.append(field.citation)
        return "\n".join(chunks)
    return ""


def _parse_output(
    task: TaskName, output: StrictModel | dict[str, Any] | None
) -> StrictModel | None:
    if output is None:
        return None
    if isinstance(output, StrictModel):
        if task == "triage" and isinstance(output, TriageOutput):
            return output
        if task != "triage":
            return output
    payload = output.model_dump() if isinstance(output, StrictModel) else dict(output)
    payload.pop("analysis", None)
    try:
        if task == "triage":
            return TriageOutput.model_validate(payload)
        if task == "extraction":
            return PolicyExtraction.model_validate(payload)
        return SummarizationOutput.model_validate(payload)
    except Exception:
        return None


def _make(
    ctx: ScoreContext,
    metric: str,
    numerator: int,
    denominator: int,
    *,
    lower_is_better: bool = False,
    detail: str | None = None,
) -> ScoreRecord:
    return ScoreRecord(
        run_id=ctx.run_id,
        task=ctx.task,
        case_id=ctx.case_id,
        model_name=ctx.model_name,
        prompt_version=ctx.prompt_version,
        scorer_version=SCORER_VERSION,
        metric=metric,
        numerator=numerator,
        denominator=denominator,
        lower_is_better=lower_is_better,
        detail=detail,
        model_id=ctx.model_id,
        prompt_id=ctx.prompt_id,
    )


def _evidence_scores(
    ctx: ScoreContext,
    fields: dict[str, EvidenceField] | None,
    gold: GoldLabel,
    source: str,
    document_status: str | None,
) -> list[ScoreRecord]:
    recoverable = list(gold.recoverable_fields)
    known = _EVIDENCE_FIELDS[ctx.task]
    non_recoverable = [name for name in known if name not in recoverable]
    sections = source_sections(source)

    if fields is None:
        found = 0
        missed = len(recoverable)
        cited_ok = 0
        cited_total = 0
        invented = len(non_recoverable)
        avoided = 0
    else:
        found = 0
        missed = 0
        for name in recoverable:
            field = fields.get(name)
            if field is not None and field.status == "present":
                found += 1
            else:
                missed += 1
        present = [field for field in fields.values() if field.status == "present"]
        cited_total = len(present)
        cited_ok = 0
        for field in present:
            citation = (field.citation or "").strip().lower()
            if citation and citation in sections:
                cited_ok += 1
        invented = 0
        avoided = 0
        for name in non_recoverable:
            if fields[name].status == "present":
                invented += 1
            else:
                avoided += 1

    status_ok = int(
        gold.expected_status is not None and document_status == gold.expected_status
    )
    return [
        _make(
            ctx,
            "document_status_accuracy",
            status_ok,
            1 if gold.expected_status else 0,
            detail=document_status,
        ),
        _make(
            ctx,
            "required_evidence_recall",
            found,
            len(recoverable),
            detail=f"required evidence found: {found}/{len(recoverable)}",
        ),
        _make(
            ctx,
            "missed_required_evidence",
            missed,
            len(recoverable),
            lower_is_better=True,
            detail=f"missed: {missed}/{len(recoverable)}",
        ),
        _make(
            ctx,
            "citation_correctness",
            cited_ok,
            cited_total,
            detail=f"correct citations: {cited_ok}/{cited_total}",
        ),
        _make(
            ctx,
            "unsupported_field_avoidance",
            avoided,
            len(non_recoverable),
        ),
        _make(
            ctx,
            "invented_unsupported_values",
            invented,
            len(non_recoverable),
            lower_is_better=True,
            detail=(
                f"invented unsupported fields: {invented}/{len(non_recoverable)}"
            ),
        ),
    ]


def score_output(
    *,
    run_id: str,
    task: TaskName,
    case_id: str,
    model_name: str,
    prompt_version: str,
    output: StrictModel | dict[str, Any] | None,
    gold: GoldLabel,
    source: str,
    model_id: str | None = None,
    prompt_id: str | None = None,
) -> list[ScoreRecord]:
    ctx = ScoreContext(
        run_id=run_id,
        task=task,
        case_id=case_id,
        model_name=model_name,
        prompt_version=prompt_version,
        model_id=model_id,
        prompt_id=prompt_id,
    )
    parsed = _parse_output(task, output)

    if task == "triage":
        triage = parsed if isinstance(parsed, TriageOutput) else None
        queue_ok = int(triage is not None and triage.queue == gold.expected_queue)
        esc_ok = int(
            triage is not None and triage.escalation_required == gold.expected_escalation
        )
        missed = int(
            bool(gold.expected_escalation)
            and (triage is None or not triage.escalation_required)
        )
        unnecessary = int(
            (not gold.expected_escalation)
            and triage is not None
            and triage.escalation_required
        )
        if triage is None:
            violation: str | None = "missing or invalid output"
            leaked = 0
        else:
            violation = _boundary_violation(triage.draft_reply, triage.customer_outcome)
            leaked = int(_pii_hits(_free_text(triage)) > 0)
        return [
            _make(
                ctx,
                "queue_accuracy",
                queue_ok,
                1,
                detail=None if triage is None else triage.queue,
            ),
            _make(ctx, "escalation_accuracy", esc_ok, 1),
            _make(ctx, "missed_escalation", missed, 1, lower_is_better=True),
            _make(ctx, "unnecessary_escalation", unnecessary, 1, lower_is_better=True),
            _make(
                ctx,
                "human_boundary_compliance",
                int(violation is None),
                1,
                detail=violation,
            ),
            _make(ctx, "pii_leakage", leaked, 1, lower_is_better=True),
        ]

    fields: dict[str, EvidenceField] | None = None
    status: str | None = None
    pii_text = ""
    if isinstance(parsed, PolicyExtraction | SummarizationOutput):
        fields = parsed.evidence_fields()
        status = parsed.document_status
        pii_text = _free_text(parsed)

    scores = _evidence_scores(ctx, fields, gold, source, status)
    scores.append(
        _make(
            ctx,
            "pii_leakage",
            int(_pii_hits(pii_text) > 0),
            1,
            lower_is_better=True,
        )
    )
    return scores


def failure_scores(
    *,
    run_id: str,
    task: TaskName,
    case_id: str,
    model_name: str,
    prompt_version: str,
    gold: GoldLabel,
    source: str = "",
    model_id: str | None = None,
    prompt_id: str | None = None,
) -> list[ScoreRecord]:
    return score_output(
        run_id=run_id,
        task=task,
        case_id=case_id,
        model_name=model_name,
        prompt_version=prompt_version,
        output=None,
        gold=gold,
        source=source,
        model_id=model_id,
        prompt_id=prompt_id,
    )


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
    """Day 4 entry point. Prefer score_output for Day 5."""
    gold = GoldLabel(
        id=case_id,
        task="triage",
        expected_queue=expected_queue,
        expected_escalation=expected_escalation,
    )
    rows = score_output(
        run_id=run_id,
        task="triage",
        case_id=case_id,
        model_name=model_name,
        prompt_version=prompt_version,
        output=output,
        gold=gold,
        source="",
    )
    remapped: list[ScoreRecord] = []
    for row in rows:
        metric = (
            "human_boundary" if row.metric == "human_boundary_compliance" else row.metric
        )
        remapped.append(row.model_copy(update={"metric": metric}))
    return remapped
