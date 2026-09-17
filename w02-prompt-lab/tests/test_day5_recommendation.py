from promptlab.config import PROJECT_ROOT, Settings
from promptlab.records import ScoreRecord, load_records
from promptlab.run import (
    _assert_scores_join,
    _load_call_records,
    counts_from_calls,
    mentioned_case_ids,
    select_task_winner,
)


def test_day5_eval_winners_are_not_single_metric() -> None:
    scores = load_records(PROJECT_ROOT / "docs" / "day5-scores.jsonl", ScoreRecord)
    names = ["mistral", "qwen"]
    repairs = {
        ("summarization", "mistral"): (4, 12),
        ("summarization", "qwen"): (0, 12),
    }
    failures = {
        ("summarization", "mistral"): (1, 12),
        ("summarization", "qwen"): (0, 12),
    }

    assert (
        select_task_winner("summarization", names, scores, repairs, failures) == "qwen"
    )
    assert select_task_winner("extraction", names, scores) == "mistral"
    assert select_task_winner("triage", names, scores) == "mistral"


def test_extraction_does_not_pick_qwen_on_recall_alone() -> None:
    scores = load_records(PROJECT_ROOT / "docs" / "day5-scores.jsonl", ScoreRecord)
    qwen_recall = sum(
        row.numerator
        for row in scores
        if row.task == "extraction"
        and row.model_name == "qwen"
        and row.metric == "required_evidence_recall"
    )
    mistral_recall = sum(
        row.numerator
        for row in scores
        if row.task == "extraction"
        and row.model_name == "mistral"
        and row.metric == "required_evidence_recall"
    )
    assert qwen_recall > mistral_recall
    assert select_task_winner("extraction", ["mistral", "qwen"], scores) == "mistral"


def test_day5_eval_scores_join_call_records() -> None:
    scores = load_records(PROJECT_ROOT / "docs" / "day5-scores.jsonl", ScoreRecord)
    calls = _load_call_records(PROJECT_ROOT / "docs" / "day5-run.jsonl")
    _assert_scores_join(scores, calls)
    version_scores = [row for row in scores if row.case_id.startswith("version:")]
    assert version_scores
    for row in version_scores:
        assert mentioned_case_ids(row)


def test_day5_eval_keeps_mistral_summarization_failure() -> None:
    calls = _load_call_records(PROJECT_ROOT / "docs" / "day5-run.jsonl")
    settings = Settings.from_env()
    model_ids = {name: config.model_id for name, config in settings.models.items()}
    repairs, failures = counts_from_calls(calls, model_ids)
    assert repairs[("summarization", "mistral")] == (4, 12)
    assert failures[("summarization", "mistral")] == (1, 12)
    assert failures[("summarization", "qwen")] == (0, 12)
    assert failures[("extraction", "mistral")] == (0, 12)
    assert failures[("extraction", "qwen")] == (0, 12)
    assert failures[("triage", "mistral")] == (0, 12)
    assert failures[("triage", "qwen")] == (0, 12)
    truncated = [
        row
        for row in calls
        if row.case_id == "S11"
        and row.task == "summarization"
        and row.model_id == model_ids["mistral"]
    ]
    assert len(truncated) == 1
    assert truncated[0].error_type == "TruncatedResponseError"
