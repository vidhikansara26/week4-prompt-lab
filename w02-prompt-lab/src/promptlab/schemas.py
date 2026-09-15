from __future__ import annotations

import json
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field

TaskName = Literal["triage", "summarization", "extraction"]
FieldStatus = Literal["present", "absent", "ambiguous"]
DocumentStatus = Literal["valid", "contradictory", "superseded", "unsupported"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceField(StrictModel):
    value: str | list[str] | None
    status: FieldStatus
    citation: str | None = None


class TriageOutput(StrictModel):
    queue: Literal[
        "card_dispute",
        "fraud_report",
        "account_servicing",
        "lending",
        "complaint",
        "escalate",
        "unsupported",
    ]
    escalation_required: bool
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    draft_reply: str
    human_review_required: Literal[True]
    customer_outcome: None = None


class SummarizationOutput(StrictModel):
    document_status: DocumentStatus
    title: EvidenceField
    version: EvidenceField
    effective_date: EvidenceField
    purpose: EvidenceField
    required_steps: EvidenceField
    exceptions: EvidenceField

    def evidence_fields(self) -> dict[str, EvidenceField]:
        return {
            "title": self.title,
            "version": self.version,
            "effective_date": self.effective_date,
            "purpose": self.purpose,
            "required_steps": self.required_steps,
            "exceptions": self.exceptions,
        }


class PolicyExtraction(StrictModel):
    document_status: DocumentStatus
    policy_name: EvidenceField
    version: EvidenceField
    effective_date: EvidenceField
    jurisdictions: EvidenceField
    beneficial_ownership_threshold: EvidenceField
    review_frequency: EvidenceField
    required_documents: EvidenceField

    def evidence_fields(self) -> dict[str, EvidenceField]:
        return {
            "policy_name": self.policy_name,
            "version": self.version,
            "effective_date": self.effective_date,
            "jurisdictions": self.jurisdictions,
            "beneficial_ownership_threshold": self.beneficial_ownership_threshold,
            "review_frequency": self.review_frequency,
            "required_documents": self.required_documents,
        }


def _example_value(annotation: Any) -> Any:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Literal:
        return args[0]
    if origin is Union or origin is UnionType:
        non_none = [arg for arg in args if arg is not type(None)]
        return _example_value(non_none[0]) if non_none else None
    if origin is list:
        return [_example_value(args[0]) if args else ""]
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return {
            name: _example_value(field.annotation)
            for name, field in annotation.model_fields.items()
        }
    if annotation is str:
        return "string from the source document"
    if annotation is bool:
        return True
    if annotation is float:
        return 0.0
    if annotation is int:
        return 0
    return None


def _literal_constraints(model: type[BaseModel]) -> str:
    lines: list[str] = []
    seen: set[type[BaseModel]] = set()

    def walk(current: type[BaseModel]) -> None:
        if current in seen:
            return
        seen.add(current)
        for name, field in current.model_fields.items():
            annotation = field.annotation
            origin = get_origin(annotation)
            if origin is Literal:
                allowed = ", ".join(json.dumps(arg) for arg in get_args(annotation))
                lines.append(f"{current.__name__}.{name} must be exactly one of: {allowed}.")
            if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                walk(annotation)

    walk(model)
    return "\n".join(lines)


def schema_description(model: type[BaseModel]) -> str:
    template = _example_value(model)
    rules = _literal_constraints(model)
    extra = ""
    if "document_status" in model.model_fields:
        extra = (
            "The prompt phrase \"non-valid\" or \"out-of-scope\" maps to "
            "document_status \"unsupported\". Never emit \"non-valid\". "
            "Meeting notes, newsletters, and agendas are unsupported. "
            "Every evidence field MUST include value, status, and citation. "
            "When status is \"absent\", emit value null and citation null. "
            "Do not omit the value key. "
            "For absent fields, value is null, not the string \"absent\". "
            "Return raw JSON only: no markdown fences and no prose.\n\n"
        )
    return (
        "Return a filled JSON instance with this shape. "
        "Do not return JSON Schema. Do not include keys named "
        "$defs, $ref, type, title, properties, or additionalProperties.\n\n"
        + extra
        + (rules + "\n\n" if rules else "")
        + json.dumps(template, indent=2)
    )


OUTPUT_SCHEMAS: dict[TaskName, type[StrictModel]] = {
    "triage": TriageOutput,
    "summarization": SummarizationOutput,
    "extraction": PolicyExtraction,
}