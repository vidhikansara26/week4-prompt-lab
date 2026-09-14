# Day 2 model comparison

Both models ran through Ollama (`provider = ollama`) under one `run_id` on all twelve summarization cases (`S01`–`S12`). Shared request fields were identical: baseline prompt `v0`, temperature `0.0`, and `max_output_tokens = 512`. Local provider charge is `$0.00` for both, so this comparison uses success, tokens, and latency only.

## Shared setup

| Field | Mistral | Qwen |
|---|---|---|
| Provider | `ollama` | `ollama` |
| Configured `model_id` | `mistral:7b` | `qwen3:8b` |
| Task | summarization | summarization |
| Cases | S01–S12 | S01–S12 |
| Prompt | baseline v0 | baseline v0 |
| Temperature | 0.0 | 0.0 |
| `max_output_tokens` | 512 | 512 |
| Attempts per case | 1 | 1 |
| `cost_usd` | 0.0 | 0.0 |

The ceiling is a safety bound, not a spend cap. It was set to **512 for both models** after Day 1’s Mistral limit of 256 truncated every Qwen case. The adapter does not retry truncation by raising `num_predict`.

## Headline metrics

| Metric | Mistral (`mistral:7b`) | Qwen (`qwen3:8b`) | What it shows |
|---|---|---|---|
| Successes | **12 / 12** | **10 / 12** | Same prompt and ceiling; Qwen still hit the cap on 2 cases |
| Failed cases | none | S01, S06 | Both failures are `TruncatedResponseError`, not retried |
| Stop reason on failures | — | `length` | Ollama reached the 512-token output ceiling |
| Total input tokens | 2919 | 2523 | Same documents; tokenizers count the prompt differently |
| Total output tokens | 1320 | 5109 | Qwen emitted ~3.9× more output tokens |
| Output range (finished cases) | 69–169 | 262–510 | Mistral stayed well under 512; Qwen used most of the budget |
| Median latency | 4378 ms | 19258.5 ms | Qwen median was ~4.4× slower |
| Max latency | 7405 ms (S01) | 24759 ms (S01) | Slowest case was S01 on both models |
| Mean output tokens / case | 110 | 426 | Qwen is the heavier generator under this prompt |

## Per-case measurements

| Case | Mistral in | Mistral out | Mistral ms | Mistral result | Qwen in | Qwen out | Qwen ms | Qwen result |
|---|---:|---:|---:|---|---:|---:|---:|---|
| S01 | 286 | 129 | 7405 | success | 247 | 512 | 24759 | truncated |
| S02 | 270 | 90 | 3913 | success | 234 | 262 | 11610 | success |
| S03 | 247 | 169 | 6564 | success | 217 | 394 | 17275 | success |
| S04 | 244 | 114 | 4534 | success | 214 | 429 | 18847 | success |
| S05 | 212 | 74 | 2976 | success | 187 | 365 | 16096 | success |
| S06 | 277 | 158 | 6369 | success | 240 | 512 | 22681 | truncated |
| S07 | 234 | 116 | 4598 | success | 200 | 447 | 19670 | success |
| S08 | 246 | 74 | 3011 | success | 210 | 490 | 21566 | success |
| S09 | 232 | 106 | 4222 | success | 202 | 473 | 20954 | success |
| S10 | 239 | 126 | 4983 | success | 205 | 510 | 22805 | success |
| S11 | 219 | 95 | 3804 | success | 186 | 386 | 17417 | success |
| S12 | 213 | 69 | 2788 | success | 181 | 329 | 14563 | success |
| **Total** | **2919** | **1320** | — | **12/12** | **2523** | **5109** | — | **10/12** |

## Observation

Under identical request settings, Mistral is the cheaper local option on **time and output tokens**: every case finished in one attempt, and no case used more than a third of the 512 ceiling. Qwen is the heavier option: it used far more output tokens and about 4× the median latency, and it still truncated on S01 and S06. Those two failures were recorded as `TruncatedResponseError` with `attempt = 1`; the adapter did not retry them with a larger ceiling.

The input-token gap (2919 vs 2523) is a tokenizer difference on the same prompt, not a different prompt. The output-token and latency gaps are the real model difference. For this lab, 512 is the justified shared cap: large enough for a two-model comparison, small enough that Qwen still hits it.