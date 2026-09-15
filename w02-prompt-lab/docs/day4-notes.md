# Day 4 notes

Shared run_id `e59c125e-4fa1-4d66-8cbb-4699d89ba842`, model `mistral:7b` through Ollama, temperature 0.0, max_output_tokens 512, 12 triage cases, provider/API cost $0.00.

`src/prompts/triage.v1.md` is frozen against this run. These v1 scores were recorded after the last v1 prompt edit. 

## triage.v1
queue correct: 11/12
escalation correct: 11/12
missed escalations: 1
unnecessary escalations: 0
human-boundary passes: 12/12

## triage.v2
queue correct: 11/12
escalation correct: 11/12
missed escalations: 1
unnecessary escalations: 0
human-boundary passes: 12/12

## Overhead
changed-queue count: 1/12 (T07 only: v1 `lending` → v2 `complaint`; gold is `escalate`)
output tokens: v1 total 1743, v2 total 2652, difference +909
median latency: v1 6274.5 ms, v2 8401 ms
maximum latency: v1 8789 ms (T01), v2 22553 ms (T10)
observation count: 24 case-runs (12 cases × 2 prompt versions)

### Output tokens and latency per case

| Case | v1 tokens | v1 ms | v2 tokens | v2 ms | token diff (v2-v1) |
|---|---:|---:|---:|---:|---:|
| T01 | 134 | 8789 | 195 | 8929 | +61 |
| T02 | 128 | 5714 | 184 | 8104 | +56 |
| T03 | 136 | 6077 | 169 | 7521 | +33 |
| T04 | 128 | 5758 | 163 | 7320 | +35 |
| T05 | 157 | 6962 | 163 | 7338 | +6 |
| T06 | 138 | 6204 | 197 | 8698 | +59 |
| T07 | 194 | 8467 | 203 | 8959 | +9 |
| T08 | 200 | 8733 | 232 | 10162 | +32 |
| T09 | 130 | 5885 | 278 | 12558 | +148 |
| T10 | 153 | 6842 | 534 | 22553 | +381 |
| T11 | 105 | 4939 | 156 | 7122 | +51 |
| T12 | 140 | 6345 | 178 | 7988 | +38 |
| **Total** | **1743** | — | **2652** | — | **+909** |

## Conclusion
Tightening `escalation_required` to follow `queue == "escalate"` removed the previous unnecessary escalations (0 on both versions). Queue accuracy is the same at 11/12. Both versions still miss T07, which mixes a loan delay with a formal complaint and should route to `escalate`. v2 used 909 extra output tokens and higher median latency for the analysis field and did not improve routing. T10 accounts for 381 of the extra tokens because v2 needed a schema repair to add `analysis`. A one-case queue disagreement on a 12-case set isnot evidence that analysis is generally better; on this run it did not earn
its overhead.
