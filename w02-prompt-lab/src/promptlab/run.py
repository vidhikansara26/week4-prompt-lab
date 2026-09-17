from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import median
from typing import cast

from pydantic import ValidationError

from promptlab.adapters.base import CompletionRequest, CompletionResult
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import PROJECT_ROOT, ModelConfig, Settings
from promptlab.corpus import GoldLabel, load_cases, validate_corpus
from promptlab.prompts import current_prompt, is_prompt_transfer, load, render_user
from promptlab.records import ScoreRecord, append_record, load_records
from promptlab.rules import VersionCandidate, select_current_version
from promptlab.schemas import (
    OUTPUT_SCHEMAS,
    PolicyExtraction,
    StrictModel,
    SummarizationOutput,
    TaskName,
    schema_description,
)
from promptlab.scoring import SCORER_VERSION, failure_scores, score_output
from promptlab.structured import complete_structured
from promptlab.usage import CallRecord

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class RecordingAdapter:
    """Count primary + repair complete() calls while delegating to OllamaAdapter."""

    def __init__(self, inner: OllamaAdapter) -> None:
        self._inner = inner
        self.provider = inner.provider
        self.model_id = inner.model_id
        self.results: list[CompletionResult] = []

    def complete(self, request: CompletionRequest, run_id: str) -> CompletionResult:
        result = self._inner.complete(request, run_id)
        self.results.append(result)
        return result

    @property
    def call_records(self) -> list[CallRecord]:
        records: list[CallRecord] = []
        for result in self.results:
            records.extend(result.records)
        return records

    @property
    def repairs(self) -> int:
        return max(len(self.results) - 1, 0)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Day 5 local two-model evaluation")
    parser.add_argument("--run-id", help="Stable identifier for this run")
    parser.add_argument("--task", choices=["triage", "summarization", "extraction"])
    parser.add_argument("--model", choices=["mistral", "qwen"])
    parser.add_argument("--limit", type=int, help="Limit cases per task")
    parser.add_argument(
        "--write-docs",
        action="store_true",
        help="Copy evidence to docs/day5-run.jsonl and docs/day5-scores.jsonl",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate configuration and corpus without calling Ollama",
    )
    parser.add_argument(
        "--rebuild-reports",
        action="store_true",
        help="Rewrite comparison.md and model-decision.md from frozen Day 5 JSONL",
    )
    return parser


def _prompt_label(task: TaskName, model_name: str) -> str:
    prompt_id, version = current_prompt(task, model_name)
    label = f"{prompt_id}.{version}"
    if is_prompt_transfer(task, model_name):
        return f"{label} transfer"
    return label


def _build_request(
    *,
    task: TaskName,
    case_id: str,
    source: str,
    model: ModelConfig,
    settings: Settings,
) -> CompletionRequest:
    prompt_id, version = current_prompt(task, model.logical_name)
    schema = OUTPUT_SCHEMAS[task]
    template = load(prompt_id, version)
    schema_text = schema_description(schema)
    system = template.system or (
        "Return only a JSON object. No markdown fences and no prose.\n\n" + schema_text
    )
    return CompletionRequest(
        task=task,
        case_id=case_id,
        prompt_id=prompt_id,
        prompt_version=version,
        system=system,
        user_content=render_user(
            template,
            {"schema_description": schema_text},
            untrusted=source,
        ),
        temperature=settings.temperature,
        max_output_tokens=model.max_output_tokens,
    )


def _version_fields(output: StrictModel) -> tuple[str, str] | None:
    if not isinstance(output, SummarizationOutput | PolicyExtraction):
        return None
    version = output.version
    effective = output.effective_date
    if (
        version.status == "present"
        and effective.status == "present"
        and isinstance(version.value, str)
        and isinstance(effective.value, str)
    ):
        return version.value, effective.value
    return None


def _add_version_scores(
    *,
    run_id: str,
    task: TaskName,
    model_name: str,
    model_id: str,
    prompt_id: str,
    prompt_version: str,
    labels: list[GoldLabel],
    outputs: dict[str, StrictModel],
    scores_path: Path,
    all_scores: list[ScoreRecord],
) -> None:
    grouped: dict[str, list[GoldLabel]] = defaultdict(list)
    for label in labels:
        if label.version_group:
            grouped[label.version_group].append(label)

    for group_name, group_labels in grouped.items():
        if len(group_labels) < 2:
            continue
        expected = next(
            (
                label.expected_current_case_id
                for label in group_labels
                if label.expected_current_case_id
            ),
            None,
        )
        as_of_raw = next((label.as_of for label in group_labels if label.as_of), None)
        if expected is None or as_of_raw is None:
            continue
        candidates: list[VersionCandidate] = []
        for label in group_labels:
            output = outputs.get(label.id)
            if output is None:
                continue
            extracted = _version_fields(output)
            if extracted is None:
                continue
            version, effective_raw = extracted
            try:
                effective = date.fromisoformat(effective_raw)
            except ValueError:
                continue
            candidates.append(
                VersionCandidate(
                    case_id=label.id,
                    version=version,
                    effective_date=effective,
                )
            )
        selected = select_current_version(candidates, date.fromisoformat(as_of_raw))
        source_ids = ",".join(label.id for label in group_labels)
        record = ScoreRecord(
            run_id=run_id,
            task=task,
            case_id=f"version:{group_name}",
            model_name=model_name,
            prompt_version=prompt_version,
            scorer_version=SCORER_VERSION,
            metric="version_selection_accuracy",
            numerator=int(selected is not None and selected.case_id == expected),
            denominator=1,
            detail=(
                f"expected={expected}; selected="
                f"{selected.case_id if selected else 'none'}; "
                f"sources={source_ids}"
            ),
            model_id=model_id,
            prompt_id=prompt_id,
        )
        append_record(scores_path, record)
        all_scores.append(record)


def _metric_sum(scores: list[ScoreRecord], metric: str) -> tuple[int, int]:
    rows = [row for row in scores if row.metric == metric]
    return sum(row.numerator for row in rows), sum(row.denominator for row in rows)


def _fmt(pair: tuple[int, int]) -> str:
    return f"{pair[0]}/{pair[1]}"


def _display_model(name: str) -> str:
    return {"mistral": "Mistral", "qwen": "Qwen"}.get(name, name)


def _quality(task: TaskName, scores: list[ScoreRecord]) -> str:
    if task == "triage":
        return (
            f"queue {_fmt(_metric_sum(scores, 'queue_accuracy'))}; "
            f"escalation {_fmt(_metric_sum(scores, 'escalation_accuracy'))}; "
            f"missed esc {_fmt(_metric_sum(scores, 'missed_escalation'))}; "
            f"unnecessary esc {_fmt(_metric_sum(scores, 'unnecessary_escalation'))}; "
            f"boundary {_fmt(_metric_sum(scores, 'human_boundary_compliance'))}; "
            f"pii {_fmt(_metric_sum(scores, 'pii_leakage'))}"
        )
    return (
        f"status {_fmt(_metric_sum(scores, 'document_status_accuracy'))}; "
        f"recall {_fmt(_metric_sum(scores, 'required_evidence_recall'))}; "
        f"missed {_fmt(_metric_sum(scores, 'missed_required_evidence'))}; "
        f"invented {_fmt(_metric_sum(scores, 'invented_unsupported_values'))}; "
        f"citations {_fmt(_metric_sum(scores, 'citation_correctness'))}; "
        f"version {_fmt(_metric_sum(scores, 'version_selection_accuracy'))}; "
        f"pii {_fmt(_metric_sum(scores, 'pii_leakage'))}"
    )


def _task_calls(
    calls: list[CallRecord],
    *,
    task: TaskName,
    model_id: str,
    prompt_id: str,
    prompt_version: str,
) -> list[CallRecord]:
    return [
        row
        for row in calls
        if row.task == task
        and row.model_id == model_id
        and row.prompt_id == prompt_id
        and row.prompt_version == prompt_version
    ]


def counts_from_calls(
    calls: list[CallRecord],
    model_ids: dict[str, str],
) -> tuple[dict[tuple[str, str], tuple[int, int]], dict[tuple[str, str], tuple[int, int]]]:
    """Count schema repairs and failed cases from recorded attempts.

    A schema repair is another complete() cycle, recorded as an extra
    attempt==1 row for the same case_id. Transport retries use attempt > 1.
    """
    id_to_name = {model_id: name for name, model_id in model_ids.items()}
    grouped: dict[tuple[str, str], list[CallRecord]] = defaultdict(list)
    for row in calls:
        name = id_to_name.get(row.model_id)
        if name is None:
            continue
        grouped[(row.task, name)].append(row)

    repairs: dict[tuple[str, str], tuple[int, int]] = {}
    failures: dict[tuple[str, str], tuple[int, int]] = {}
    for key, rows in grouped.items():
        by_case: dict[str, list[CallRecord]] = defaultdict(list)
        for row in rows:
            by_case[row.case_id].append(row)
        n_cases = len(by_case)
        repaired_cases = 0
        failed_cases = 0
        for case_rows in by_case.values():
            primaries = [row for row in case_rows if row.attempt == 1]
            if len(primaries) > 1:
                repaired_cases += 1
            if not any(row.error_type is None for row in case_rows):
                failed_cases += 1
        repairs[key] = (repaired_cases, n_cases)
        failures[key] = (failed_cases, n_cases)
    return repairs, failures


_JOIN_CASE_RE = re.compile(r"(?:expected|selected|sources)=([A-Za-z0-9,_:-]+)")


def mentioned_case_ids(score: ScoreRecord) -> set[str]:
    """Case ids a score record must join to in the call log."""
    if not score.case_id.startswith("version:"):
        return {score.case_id}
    mentioned: set[str] = set()
    for match in _JOIN_CASE_RE.finditer(score.detail or ""):
        for part in match.group(1).split(","):
            value = part.strip()
            if value and value != "none":
                mentioned.add(value)
    return mentioned


def _model_metric(
    scores: list[ScoreRecord], model_name: str, metric: str
) -> tuple[int, int]:
    return _metric_sum(
        [row for row in scores if row.model_name == model_name],
        metric,
    )


def select_task_winner(
    task: TaskName,
    model_names: list[str],
    scores: list[ScoreRecord],
    repairs_by_key: dict[tuple[str, str], tuple[int, int]] | None = None,
    failures_by_key: dict[tuple[str, str], tuple[int, int]] | None = None,
) -> str:
    """Pick a per-task model without ranking on a single numerator.

    Extraction keeps missed and invented separate. Triage does not treat a
    one-case queue gap as a ranking. Summarization weighs status, citations,
    repairs, and failures together.
    """
    if not model_names:
        raise ValueError("model_names must not be empty")
    task_scores = [row for row in scores if row.task == task]
    repairs = repairs_by_key or {}
    failures = failures_by_key or {}

    def sort_key(model_name: str) -> tuple[int, ...]:
        if task == "extraction":
            recall, _ = _model_metric(task_scores, model_name, "required_evidence_recall")
            invented, _ = _model_metric(
                task_scores, model_name, "invented_unsupported_values"
            )
            missed, _ = _model_metric(task_scores, model_name, "missed_required_evidence")
            best_recall = max(
                _model_metric(task_scores, name, "required_evidence_recall")[0]
                for name in model_names
            )
            close = int(best_recall - recall <= 1)
            # Close-recall models: fewer invented fields first.
            return (close, -invented, recall, -missed)
        if task == "triage":
            boundary, _ = _model_metric(
                task_scores, model_name, "human_boundary_compliance"
            )
            unnecessary, _ = _model_metric(
                task_scores, model_name, "unnecessary_escalation"
            )
            missed_esc, _ = _model_metric(task_scores, model_name, "missed_escalation")
            queue, _ = _model_metric(task_scores, model_name, "queue_accuracy")
            return (boundary, -unnecessary, -missed_esc, queue)
        status, _ = _model_metric(task_scores, model_name, "document_status_accuracy")
        citations, _ = _model_metric(task_scores, model_name, "citation_correctness")
        recall, _ = _model_metric(task_scores, model_name, "required_evidence_recall")
        invented, _ = _model_metric(
            task_scores, model_name, "invented_unsupported_values"
        )
        repaired = repairs.get((task, model_name), (0, 0))[0]
        failed = failures.get((task, model_name), (0, 0))[0]
        return (status, citations, recall, -invented, -repaired, -failed)

    return max(model_names, key=sort_key)


def _recommendation_reason(
    task: TaskName,
    winner: str,
    model_names: list[str],
    scores: list[ScoreRecord],
    repairs_by_key: dict[tuple[str, str], tuple[int, int]],
) -> str:
    others = [name for name in model_names if name != winner]
    other = others[0] if others else None
    winner_scores = [row for row in scores if row.task == task and row.model_name == winner]
    if task == "summarization":
        other_scores = [
            row for row in scores if row.task == task and row.model_name == other
        ]
        winner_repairs = repairs_by_key.get((task, winner), (0, 0))
        other_repairs = repairs_by_key.get((task, other or ""), (0, 0))
        reason = (
            f"{_fmt(_metric_sum(winner_scores, 'document_status_accuracy'))} status, "
            f"{_fmt(_metric_sum(winner_scores, 'required_evidence_recall'))} "
            f"required-evidence recall, "
            f"{_fmt(_metric_sum(winner_scores, 'citation_correctness'))} citation "
            f"correctness, repairs {winner_repairs[0]}/{winner_repairs[1]}"
        )
        if other is not None:
            reason += (
                f", versus {_display_model(other)} "
                f"{_fmt(_metric_sum(other_scores, 'document_status_accuracy'))} status, "
                f"{_fmt(_metric_sum(other_scores, 'citation_correctness'))} citations, "
                f"and {other_repairs[0]}/{other_repairs[1]} repairs"
            )
        return (
            reason + "; **reopen if:** a Qwen-adapted summarization prompt is measured "
            "or Mistral citation format is fixed without losing recall"
        )
    if task == "extraction":
        other_scores = [
            row for row in scores if row.task == task and row.model_name == other
        ]
        reason = (
            f"missed {_fmt(_metric_sum(winner_scores, 'missed_required_evidence'))} "
            f"and invented "
            f"{_fmt(_metric_sum(winner_scores, 'invented_unsupported_values'))}"
        )
        if other is not None:
            reason += (
                f", versus {_display_model(other)} `{_prompt_label(task, other)}` missed "
                f"{_fmt(_metric_sum(other_scores, 'missed_required_evidence'))} "
                f"but invented "
                f"{_fmt(_metric_sum(other_scores, 'invented_unsupported_values'))}; "
                "keep missed and invented separate and prefer fewer unsupported "
                "fields on this 12-case set"
            )
        return (
            reason + "; **reopen if:** Qwen `extract.v3` invented fields drop without "
            "losing recall, or `extract.v2` is transferred to Qwen with thinking off"
        )
    other_scores = [
        row for row in scores if row.task == task and row.model_name == other
    ]
    reason = (
        f"human-boundary "
        f"{_fmt(_metric_sum(winner_scores, 'human_boundary_compliance'))}"
    )
    if other is not None:
        reason += (
            f" on both models; {_display_model(winner)} unnecessary escalations "
            f"{_fmt(_metric_sum(winner_scores, 'unnecessary_escalation'))} versus "
            f"{_display_model(other)} {_fmt(_metric_sum(other_scores, 'unnecessary_escalation'))}; "
            f"queue {_fmt(_metric_sum(winner_scores, 'queue_accuracy'))} versus "
            f"{_fmt(_metric_sum(other_scores, 'queue_accuracy'))} is a one-case gap "
            "and is not a ranking by itself"
        )
    return (
        reason + "; **reopen if:** Qwen unnecessary escalations return to 0/12 on a "
        "larger set, or Day 4's `triage.v2` is re-measured on Qwen"
    )


def _write_reports(
    *,
    run_id: str,
    tasks: list[TaskName],
    model_names: list[str],
    model_ids: dict[str, str],
    scores: list[ScoreRecord],
    calls: list[CallRecord],
    repairs_by_key: dict[tuple[str, str], tuple[int, int]],
    failures_by_key: dict[tuple[str, str], tuple[int, int]],
    report_path: Path,
    decision_path: Path,
) -> None:
    lines = [
        "# Local Model Comparison",
        "",
        f"Run ID: `{run_id}`",
        "",
        "Both models use `provider = \"ollama\"` and `cost_usd = $0.00`.",
        "Counts are reported with denominators. Latency uses median and maximum, "
        "not the mean.",
        "Human-boundary compliance was scored for both Mistral and Qwen.",
        "",
    ]
    for task in tasks:
        lines.extend(
            [
                f"## {task.title()}",
                "",
                "| Model | Prompt | Quality | Input tokens/case | Output tokens/case | "
                "Median latency | Max latency | n | Repairs | Failed attempts |",
                "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for model_name in model_names:
            prompt_id, version = current_prompt(task, model_name)
            model_id = model_ids[model_name]
            task_scores = [
                row
                for row in scores
                if row.task == task and row.model_name == model_name
            ]
            task_calls = _task_calls(
                calls,
                task=task,
                model_id=model_id,
                prompt_id=prompt_id,
                prompt_version=version,
            )
            n_cases = len({row.case_id for row in task_calls})
            latencies = [float(row.latency_ms) for row in task_calls]
            in_tok = sum(row.input_tokens for row in task_calls)
            out_tok = sum(row.output_tokens for row in task_calls)
            in_per = round(in_tok / n_cases) if n_cases else 0
            out_per = round(out_tok / n_cases) if n_cases else 0
            med = f"{median(latencies):.0f} ms" if latencies else "—"
            mx = f"{max(latencies):.0f} ms" if latencies else "—"
            repaired, total = repairs_by_key.get((task, model_name), (0, 0))
            failed, _ = failures_by_key.get((task, model_name), (0, 0))
            retries = sum(1 for row in task_calls if row.attempt > 1)
            error_attempts = sum(1 for row in task_calls if row.error_type)
            lines.append(
                f"| {model_name} | {_prompt_label(task, model_name)} | "
                f"{_quality(task, task_scores)} | {in_per} | {out_per} | "
                f"{med} | {mx} | {len(latencies)} | {repaired}/{total} | "
                f"{failed} cases / {error_attempts} failed attempts "
                f"(+{retries} retries) |"
            )
        lines.append("")

    lines.extend(
        [
            "## Limits",
            "",
            "- There are only 12 cases per task. Results are directional, not "
            "production-scale estimates.",
            "- Prompt-transfer rows are labeled `transfer`. They measure that "
            "specific prompt on the second model, not the model's best adapted prompt.",
            "- Qwen extraction used adapted `extract.v3`, not a transfer of "
            "`extract.v2`.",
            "- Qwen3 thinking was disabled through model config (`think=false`). "
            "Mistral omits the think flag. Both used a 512 output-token cap.",
            "- Mistral summarization's 1 failed attempt is S11 "
            "`TruncatedResponseError` under frozen `summarize.v1` (extra keys until "
            "the 512-token cap). That is scored evidence, not an adapter failure.",
            "- Untested combinations: `extract.v2` transferred to Qwen; `extract.v3` "
            "on Mistral; `triage.v2` on either model; a Qwen-adapted summarization "
            "prompt; Qwen thinking-on at 512 tokens as a successful scored run.",
            "- No production-volume reliability claim is being made.",
            "- Local Ollama latency depends on this lab's hardware.",
            "- An 11/12 vs 10/12 gap on this sample is not a universal model ranking.",
            "- `missed` and `invented` denominators are evidence-field slots across "
            "the 12 cases, not a case-level rate.",
            "- Extra JSONL rows with the same case_id and `attempt=1` are schema "
            "repairs. Transport retries use `attempt > 1`.",
            "- Provider/API cost is `$0.00` for both models. Comparison uses quality, "
            "tokens, latency, repairs, and failures.",
            "",
            "## Recommendation",
            "",
        ]
    )

    decision_lines = [
        "# Model Decision Record",
        "",
        f"Run ID: `{run_id}`",
        "",
        "Do not rewrite earlier decision constraints after seeing these results. "
        "Day 4 selected `triage.v1` over `triage.v2` on Mistral because routing was "
        "unchanged and v2 added token and latency overhead.",
        "",
        "## Evidence",
        "",
    ]
    for task in tasks:
        for model_name in model_names:
            task_scores = [
                row
                for row in scores
                if row.task == task and row.model_name == model_name
            ]
            extra = ""
            if task != "triage":
                repaired, total = repairs_by_key.get((task, model_name), (0, 0))
                extra = f"; repairs {repaired}/{total}"
            decision_lines.append(
                f"- `{task}` / {model_name} / `{_prompt_label(task, model_name)}`: "
                f"{_quality(task, task_scores)}{extra}"
            )
    decision_lines.extend(
        [
            "",
            "Qwen3 ran with config `think=false` and `max_output_tokens=512`. "
            "Mistral omitted the think flag and used the same 512 cap. "
            "Provider/API cost is `$0.00`.",
            "",
            "## Decision",
            "",
        ]
    )

    for task in tasks:
        best = select_task_winner(
            task,
            model_names,
            scores,
            repairs_by_key=repairs_by_key,
            failures_by_key=failures_by_key,
        )
        reason = _recommendation_reason(
            task, best, model_names, scores, repairs_by_key
        )
        rejected = ", ".join(
            f"{name} (`{_prompt_label(task, name)}`)"
            for name in model_names
            if name != best
        )
        rec = (
            f"- **task:** {task}; **model:** {best}; "
            f"**prompt:** `{_prompt_label(task, best)}`; "
            f"**reason:** {reason}."
        )
        lines.append(rec)
        decision_lines.append(rec)
        decision_lines.append(f"  - rejected alternatives: {rejected or 'none'}")

    lines.append("")
    decision_lines.extend(
        [
            "",
            "## Rejected alternatives",
            "",
            "- A single global model for all three tasks. Each task is decided from "
            "its own measured row.",
            "- `triage.v2` as the Day 5 triage prompt. Day 4 showed the same 11/12 "
            "routing with higher tokens and latency.",
            "- `baseline.v0` as a Day 5 task prompt.",
            "- Selecting Qwen for extraction on recall alone while ignoring "
            "invented/unsupported fields.",
            "- Selecting Qwen for triage on queue 12/12 alone while ignoring "
            "unnecessary escalations.",
            "- Editing frozen `summarize.v1` after `day5-eval` recorded results.",
            "- A Qwen thinking-on configuration at 512 tokens, which truncated in "
            "the aborted `day5-full` run.",
            "",
            "## Review triggers",
            "",
            "- New gold cases are added.",
            "- `extract.v2` is run on Qwen with thinking off.",
            "- `extract.v3` invented/unsupported counts drop on a new measured run.",
            "- Human-boundary compliance drops below 12/12 on either tested model "
            "(Mistral, Qwen).",
            "- Repair rate or truncation rises enough to change the quality ranking.",
            "",
        ]
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    decision_path.write_text("\n".join(decision_lines) + "\n", encoding="utf-8")


def _load_call_records(path: Path) -> list[CallRecord]:
    if not path.exists():
        return []
    records: list[CallRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(CallRecord.model_validate(json.loads(line)))
    return records


def _call_matches(score: ScoreRecord, row: CallRecord, case_id: str) -> bool:
    return (
        row.run_id == score.run_id
        and row.case_id == case_id
        and row.task == score.task
        and row.model_id == score.model_id
        and row.prompt_id == score.prompt_id
        and row.prompt_version == score.prompt_version
    )


def _assert_scores_join(scores: list[ScoreRecord], calls: list[CallRecord]) -> None:
    unmatched = 0
    for score in scores:
        case_ids = mentioned_case_ids(score)
        found = bool(case_ids) and all(
            any(_call_matches(score, row, case_id) for row in calls)
            for case_id in case_ids
        )
        if not found:
            unmatched += 1
    if unmatched:
        raise SystemExit(f"{unmatched} score records did not join to call evidence")


def _rebuild_reports_from_docs(run_id: str) -> None:
    settings = Settings.from_env()
    model_ids = {name: config.model_id for name, config in settings.models.items()}
    calls = [
        row
        for row in _load_call_records(PROJECT_ROOT / "docs" / "day5-run.jsonl")
        if row.run_id == run_id
    ]
    scores = [
        row
        for row in load_records(PROJECT_ROOT / "docs" / "day5-scores.jsonl", ScoreRecord)
        if row.run_id == run_id
    ]
    if not calls or not scores:
        raise SystemExit(f"no frozen Day 5 records found for run_id {run_id!r}")
    _assert_scores_join(scores, calls)
    task_order: list[TaskName] = ["summarization", "extraction", "triage"]
    present = {row.task for row in scores}
    tasks = [task for task in task_order if task in present]
    model_names = [name for name in settings.models if name in {row.model_name for row in scores}]
    repairs_by_key, failures_by_key = counts_from_calls(calls, model_ids)
    _write_reports(
        run_id=run_id,
        tasks=tasks,
        model_names=model_names,
        model_ids=model_ids,
        scores=scores,
        calls=calls,
        repairs_by_key=repairs_by_key,
        failures_by_key=failures_by_key,
        report_path=PROJECT_ROOT / "reports" / "comparison.md",
        decision_path=PROJECT_ROOT / "docs" / "model-decision.md",
    )
    print(f"Report: {PROJECT_ROOT / 'reports' / 'comparison.md'}")
    print(f"Decision: {PROJECT_ROOT / 'docs' / 'model-decision.md'}")


def main() -> None:
    os.chdir(PROJECT_ROOT)
    args = _parser().parse_args()
    counts = validate_corpus()
    if args.validate_only:
        print("Corpus valid: " + ", ".join(f"{task}={count}" for task, count in counts.items()))
        return

    if args.rebuild_reports:
        frozen_run_id = cast(str | None, args.run_id) or "day5-eval"
        if not RUN_ID_PATTERN.fullmatch(frozen_run_id):
            raise SystemExit("invalid --run-id")
        _rebuild_reports_from_docs(frozen_run_id)
        return

    run_id = cast(str | None, args.run_id)
    if run_id is None or not RUN_ID_PATTERN.fullmatch(run_id):
        raise SystemExit(
            "--run-id is required and must use letters, numbers, '.', '_' or '-'"
        )
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be at least 1")

    selected_tasks: list[TaskName]
    if args.task:
        selected_tasks = [cast(TaskName, args.task)]
    else:
        selected_tasks = ["summarization", "extraction", "triage"]

    settings = Settings.from_env()
    selected_models = [cast(str, args.model)] if args.model else list(settings.models)
    model_ids = {name: settings.models[name].model_id for name in selected_models}

    run_file = PROJECT_ROOT / "runs" / f"{run_id}.jsonl"
    if run_file.exists():
        raise SystemExit(f"Run file already exists: {run_file}")

    scores_path = PROJECT_ROOT / "runs" / f"{run_id}-scores.jsonl"
    scores_path.parent.mkdir(parents=True, exist_ok=True)

    all_scores: list[ScoreRecord] = []
    outputs_by_key: dict[tuple[TaskName, str], dict[str, StrictModel]] = defaultdict(dict)
    labels_by_task: dict[TaskName, list[GoldLabel]] = {}
    repairs_by_key: dict[tuple[str, str], tuple[int, int]] = {}
    failures_by_key: dict[tuple[str, str], tuple[int, int]] = {}

    adapters: dict[str, OllamaAdapter] = {
        name: OllamaAdapter(model_id=settings.models[name].model_id)
        for name in selected_models
    }

    for task in selected_tasks:
        pairs = load_cases(task)
        if args.limit is not None:
            pairs = pairs[: args.limit]
        labels_by_task[task] = [gold for _case, gold in pairs]
        for model_name in selected_models:
            model: ModelConfig = settings.models[model_name]
            prompt_id, version = current_prompt(task, model_name)
            repaired_cases = 0
            failed_cases = 0
            for case, gold in pairs:
                recorder = RecordingAdapter(adapters[model_name])
                request = _build_request(
                    task=task,
                    case_id=case.id,
                    source=case.document_text,
                    model=model,
                    settings=settings,
                )
                parsed: StrictModel | None = None
                try:
                    parsed = complete_structured(
                        recorder,
                        request,
                        OUTPUT_SCHEMAS[task],
                        run_id,
                        max_repairs=settings.max_schema_repairs,
                    )
                    outputs_by_key[(task, model_name)][case.id] = parsed
                    case_scores = score_output(
                        run_id=run_id,
                        task=task,
                        case_id=case.id,
                        model_name=model_name,
                        prompt_version=version,
                        output=parsed,
                        gold=gold,
                        source=case.document_text,
                        model_id=model.model_id,
                        prompt_id=prompt_id,
                    )
                except (
                    RuntimeError,
                    ValidationError,
                    json.JSONDecodeError,
                    ValueError,
                ):
                    failed_cases += 1
                    case_scores = failure_scores(
                        run_id=run_id,
                        task=task,
                        case_id=case.id,
                        model_name=model_name,
                        prompt_version=version,
                        gold=gold,
                        source=case.document_text,
                        model_id=model.model_id,
                        prompt_id=prompt_id,
                    )
                if recorder.repairs:
                    repaired_cases += 1
                for score in case_scores:
                    append_record(scores_path, score)
                    all_scores.append(score)
                print(
                    f"{task:13} {model_name:8} {case.id:5} "
                    f"{'ok' if parsed is not None else 'failed'} "
                    f"repairs={recorder.repairs}"
                )
            repairs_by_key[(task, model_name)] = (repaired_cases, len(pairs))
            failures_by_key[(task, model_name)] = (failed_cases, len(pairs))
            if task != "triage":
                _add_version_scores(
                    run_id=run_id,
                    task=task,
                    model_name=model_name,
                    model_id=model.model_id,
                    prompt_id=prompt_id,
                    prompt_version=version,
                    labels=labels_by_task[task],
                    outputs=outputs_by_key[(task, model_name)],
                    scores_path=scores_path,
                    all_scores=all_scores,
                )

    all_calls = _load_call_records(run_file)
    _assert_scores_join(all_scores, all_calls)
    repairs_by_key, failures_by_key = counts_from_calls(all_calls, model_ids)

    full_run = args.task is None and args.model is None and args.limit is None
    if args.write_docs and not full_run:
        raise SystemExit(
            "--write-docs requires all three tasks and both models with no --limit"
        )

    if full_run:
        _write_reports(
            run_id=run_id,
            tasks=selected_tasks,
            model_names=selected_models,
            model_ids=model_ids,
            scores=all_scores,
            calls=all_calls,
            repairs_by_key=repairs_by_key,
            failures_by_key=failures_by_key,
            report_path=PROJECT_ROOT / "reports" / "comparison.md",
            decision_path=PROJECT_ROOT / "docs" / "model-decision.md",
        )
        print(f"Report: {PROJECT_ROOT / 'reports' / 'comparison.md'}")
    else:
        print("Partial run: not overwriting reports/comparison.md")

    if args.write_docs:
        docs_run = PROJECT_ROOT / "docs" / "day5-run.jsonl"
        docs_scores = PROJECT_ROOT / "docs" / "day5-scores.jsonl"
        shutil.copyfile(run_file, docs_run)
        shutil.copyfile(scores_path, docs_scores)
        print(f"Wrote {docs_run}")
        print(f"Wrote {docs_scores}")

    print("Recorded provider cost: $0.00")


if __name__ == "__main__":
    main()
