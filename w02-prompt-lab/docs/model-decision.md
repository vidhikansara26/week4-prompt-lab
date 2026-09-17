# Model Decision Record

Run ID: `day5-eval`

Do not rewrite earlier decision constraints after seeing these results. Day 4 selected `triage.v1` over `triage.v2` on Mistral because routing was unchanged and v2 added token and latency overhead.

## Evidence

- `summarization` / mistral / `summarize.v1`: status 10/12; recall 54/60; missed 6/60; invented 6/12; citations 18/60; version 1/1; pii 0/12; repairs 4/12
- `summarization` / qwen / `summarize.v1 transfer`: status 12/12; recall 60/60; missed 0/60; invented 3/12; citations 63/63; version 1/1; pii 0/12; repairs 0/12
- `extraction` / mistral / `extract.v2`: status 9/12; recall 71/72; missed 1/72; invented 2/12; citations 73/73; version 1/1; pii 0/12; repairs 0/12
- `extraction` / qwen / `extract.v3`: status 10/12; recall 72/72; missed 0/72; invented 7/12; citations 79/79; version 1/1; pii 0/12; repairs 0/12
- `triage` / mistral / `triage.v1`: queue 11/12; escalation 11/12; missed esc 1/12; unnecessary esc 0/12; boundary 12/12; pii 0/12
- `triage` / qwen / `triage.v1 transfer`: queue 12/12; escalation 10/12; missed esc 0/12; unnecessary esc 2/12; boundary 12/12; pii 0/12

Qwen3 ran with config `think=false` and `max_output_tokens=512`. Mistral omitted the think flag and used the same 512 cap. Provider/API cost is `$0.00`.

## Decision

- **task:** summarization; **model:** qwen; **prompt:** `summarize.v1 transfer`; **reason:** 12/12 status, 60/60 required-evidence recall, 63/63 citation correctness, repairs 0/12, versus Mistral 10/12 status, 18/60 citations, and 4/12 repairs; **reopen if:** a Qwen-adapted summarization prompt is measured or Mistral citation format is fixed without losing recall.
  - rejected alternatives: mistral (`summarize.v1`)
- **task:** extraction; **model:** mistral; **prompt:** `extract.v2`; **reason:** missed 1/72 and invented 2/12, versus Qwen `extract.v3` missed 0/72 but invented 7/12; keep missed and invented separate and prefer fewer unsupported fields on this 12-case set; **reopen if:** Qwen `extract.v3` invented fields drop without losing recall, or `extract.v2` is transferred to Qwen with thinking off.
  - rejected alternatives: qwen (`extract.v3`)
- **task:** triage; **model:** mistral; **prompt:** `triage.v1`; **reason:** human-boundary 12/12 on both models; Mistral unnecessary escalations 0/12 versus Qwen 2/12; queue 11/12 versus 12/12 is a one-case gap and is not a ranking by itself; **reopen if:** Qwen unnecessary escalations return to 0/12 on a larger set, or Day 4's `triage.v2` is re-measured on Qwen.
  - rejected alternatives: qwen (`triage.v1 transfer`)

## Rejected alternatives

- A single global model for all three tasks. Each task is decided from its own measured row.
- `triage.v2` as the Day 5 triage prompt. Day 4 showed the same 11/12 routing with higher tokens and latency.
- `baseline.v0` as a Day 5 task prompt.
- Selecting Qwen for extraction on recall alone while ignoring invented/unsupported fields.
- Selecting Qwen for triage on queue 12/12 alone while ignoring unnecessary escalations.
- Editing frozen `summarize.v1` after `day5-eval` recorded results.
- A Qwen thinking-on configuration at 512 tokens, which truncated in the aborted `day5-full` run.

## Review triggers

- New gold cases are added.
- `extract.v2` is run on Qwen with thinking off.
- `extract.v3` invented/unsupported counts drop on a new measured run.
- Human-boundary compliance drops below 12/12 on either tested model (Mistral, Qwen).
- Repair rate or truncation rises enough to change the quality ranking.

