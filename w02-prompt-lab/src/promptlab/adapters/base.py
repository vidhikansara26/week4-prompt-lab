from typing import Literal, Protocol

from pydantic import BaseModel

from promptlab.usage import CallRecord


class CompletionRequest(BaseModel):
    """Input to every model call in a prompt portfolio."""
    task: Literal["triage", "summarization", "extraction"]
    case_id: str
    prompt_id: str
    prompt_version: str
    system: str
    user_content: str
    temperature: float
    max_output_tokens: int


class CompletionResult(BaseModel):
    """Output from every model call in a prompt portfolio."""
    succeeded: bool
    text: str | None
    error_type: str | None
    records: list[CallRecord]


class ModelAdapter(Protocol):
    """Interface for all model adapters."""
    provider: str
    model_id: str

    def complete(
        self,
        request: CompletionRequest,
        run_id: str,
    ) -> CompletionResult: ...