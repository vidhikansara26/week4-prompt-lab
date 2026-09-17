# Local Model Comparison

Run ID: `day5-eval`

Both models use `provider = "ollama"` and `cost_usd = $0.00`.
Counts are reported with denominators. Latency uses median and maximum, not the mean.
Human-boundary compliance was scored for both Mistral and Qwen.

## Summarization

| Model | Prompt | Quality | Input tokens/case | Output tokens/case | Median latency | Max latency | n | Repairs | Failed attempts |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| mistral | summarize.v1 | status 10/12; recall 54/60; missed 6/60; invented 6/12; citations 18/60; version 1/1; pii 0/12 | 1474 | 463 | 13344 ms | 20217 ms | 16 | 4/12 | 1 cases / 1 failed attempts (+0 retries) |
| qwen | summarize.v1 transfer | status 12/12; recall 60/60; missed 0/60; invented 3/12; citations 63/63; version 1/1; pii 0/12 | 833 | 251 | 11579 ms | 16181 ms | 12 | 0/12 | 0 cases / 0 failed attempts (+0 retries) |

## Extraction

| Model | Prompt | Quality | Input tokens/case | Output tokens/case | Median latency | Max latency | n | Repairs | Failed attempts |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| mistral | extract.v2 | status 9/12; recall 71/72; missed 1/72; invented 2/12; citations 73/73; version 1/1; pii 0/12 | 2504 | 345 | 17910 ms | 21320 ms | 12 | 0/12 | 0 cases / 0 failed attempts (+0 retries) |
| qwen | extract.v3 | status 10/12; recall 72/72; missed 0/72; invented 7/12; citations 79/79; version 1/1; pii 0/12 | 875 | 282 | 13603 ms | 16955 ms | 12 | 0/12 | 0 cases / 0 failed attempts (+0 retries) |

## Triage

| Model | Prompt | Quality | Input tokens/case | Output tokens/case | Median latency | Max latency | n | Repairs | Failed attempts |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| mistral | triage.v1 | queue 11/12; escalation 11/12; missed esc 1/12; unnecessary esc 0/12; boundary 12/12; pii 0/12 | 954 | 145 | 6116 ms | 9115 ms | 12 | 0/12 | 0 cases / 0 failed attempts (+0 retries) |
| qwen | triage.v1 transfer | queue 12/12; escalation 10/12; missed esc 0/12; unnecessary esc 2/12; boundary 12/12; pii 0/12 | 805 | 115 | 5372 ms | 9371 ms | 12 | 0/12 | 0 cases / 0 failed attempts (+0 retries) |

## Limits

- There are only 12 cases per task. Results are directional, not production-scale estimates.
- Prompt-transfer rows are labeled `transfer`. They measure that specific prompt on the second model, not the model's best adapted prompt.
- Qwen extraction used adapted `extract.v3`, not a transfer of `extract.v2`.
- Qwen3 thinking was disabled through model config (`think=false`). Mistral omits the think flag. Both used a 512 output-token cap.
- Mistral summarization's 1 failed attempt is S11 `TruncatedResponseError` under frozen `summarize.v1` (extra keys until the 512-token cap). That is scored evidence, not an adapter failure.
- Untested combinations: `extract.v2` transferred to Qwen; `extract.v3` on Mistral; `triage.v2` on either model; a Qwen-adapted summarization prompt; Qwen thinking-on at 512 tokens as a successful scored run.
- No production-volume reliability claim is being made.
- Local Ollama latency depends on this lab's hardware.
- An 11/12 vs 10/12 gap on this sample is not a universal model ranking.
- `missed` and `invented` denominators are evidence-field slots across the 12 cases, not a case-level rate.
- Extra JSONL rows with the same case_id and `attempt=1` are schema repairs. Transport retries use `attempt > 1`.
- Provider/API cost is `$0.00` for both models. Comparison uses quality, tokens, latency, repairs, and failures.

## Recommendation

- **task:** summarization; **model:** qwen; **prompt:** `summarize.v1 transfer`; **reason:** 12/12 status, 60/60 required-evidence recall, 63/63 citation correctness, repairs 0/12, versus Mistral 10/12 status, 18/60 citations, and 4/12 repairs; **reopen if:** a Qwen-adapted summarization prompt is measured or Mistral citation format is fixed without losing recall.
- **task:** extraction; **model:** mistral; **prompt:** `extract.v2`; **reason:** missed 1/72 and invented 2/12, versus Qwen `extract.v3` missed 0/72 but invented 7/12; keep missed and invented separate and prefer fewer unsupported fields on this 12-case set; **reopen if:** Qwen `extract.v3` invented fields drop without losing recall, or `extract.v2` is transferred to Qwen with thinking off.
- **task:** triage; **model:** mistral; **prompt:** `triage.v1`; **reason:** human-boundary 12/12 on both models; Mistral unnecessary escalations 0/12 versus Qwen 2/12; queue 11/12 versus 12/12 is a one-case gap and is not a ranking by itself; **reopen if:** Qwen unnecessary escalations return to 0/12 on a larger set, or Day 4's `triage.v2` is re-measured on Qwen.

