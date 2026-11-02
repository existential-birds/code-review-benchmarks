# AACR baseline

First full run of Daydream against AACR-Bench, on 2026-11-01. It's the reference
point for later Daydream versions. See [the AACR guide](../benchmarks/aacr/README.md)
for how the benchmark works and how to rerun it.

## Results

| Metric | Score | Raw counts |
|---|---:|---|
| Precision | **0.263** | 93 of 354 findings matched a reference |
| Recall | **0.081** | 98 of 1,213 references covered |
| Balanced score | **0.124** | |

These scores measure agreement with AACR's annotated comments, not verified
bug-finding accuracy. Findings that match no comment aren't necessarily wrong.
Recall is limited by volume: Daydream reported 354 findings against 1,213
references.

## Coverage

| | Cases | References |
|---|---:|---:|
| AACR catalog | 196 | 1,506 |
| Unavailable: pinned commit no longer exists upstream | 11 | 87 |
| Run | 185 | 1,419 |
| Failed, not scored | 18 | 206 |
| **Scored** | **167** | **1,213** |

Failed cases are outside the scores above.

## Failures

No trial was retried or cancelled. 17 of the 18 failures are reviews that ended
incomplete: Daydream finished, but at least one part of the review wasn't covered.
The benchmark doesn't score those, though most retained some findings.

| Cause | Trials | Status |
|---|---:|---|
| `Frozen source attribution rejected: evidence_incomplete` | 15 | [#1744](https://github.com/existential-birds/daydream/issues/1744) for the four trials traced; the other 11 weren't traced to a root cause |
| `Stage coverage or candidate decisions remain incomplete` | 1 | [#1745](https://github.com/existential-birds/daydream/issues/1745) |
| `Review limit exhausted: tool_call_budget_exceeded` | 1 | [#1746](https://github.com/existential-birds/daydream/issues/1746) |
| `Pinned comparison differs from repository` | 1 | Benchmark bug, fixed |

Failed cases by cause:

- **Source attribution:** ClickHouse #75218, Elasticsearch #119759, GoFr #1617,
  Gemini CLI #8797, Immich #16893, Keycloak #40283, TypeScript-Go #1446, mpv #16061,
  LocalAI #4577, n8n #20188, Ollama #9654, Ollama #9746, Codex #3212,
  TimescaleDB #8138, Waveterm #1755. ClickHouse #75218 also hit the stage coverage
  failure, and Ollama #9654 also hit `Stage evidence admission failed`.
- **Stage coverage:** ClickHouse #82441.
- **Tool budget:** Checkmate #2883.
- **Comparison mismatch:** Electron #46982.

The comparison mismatch came from generating the Electron task's diff with Apple
Git, which formats `.mm` hunk headers differently from the container's Git. After
the fix, a separate rerun completed with no findings against five references. That
rerun isn't included in the scores or usage on this page.

## Time and usage

| Measure | Value |
|---|---:|
| Job duration, two concurrent trials | 7h 45m |
| Review time, median | 198 seconds |
| Review time, longest | 1,749 seconds |
| Reviewer input tokens | 392,678,781 |
| Of those, cached | 315,383,936 |
| Reviewer output tokens | 3,704,797 |
| Judge calls | 284 |
| Judge input and output tokens | 193,341 and 20,926 |

Reviewer usage includes the failed trials. Both the reviewer and judge used
subscriptions, so the actual cost is unknown. API-equivalent estimates are $290.36
for the reviewer and $0.60 for the judge.

## How it was run

| Setting | Value |
|---|---|
| Daydream | 0.30.0, `5bae1ac09918ecf8d095aaf6d283235ca1ad83a8` |
| Wheel SHA-256 | `923075dff2be5e6d5b2bdc670eb5e247fc86efc391ae5d6c23eee35e4ca63d92` |
| Review configuration | Codex backend, default profile and models, precision off, review only |
| Reviewer models | `gpt-6.1-sol`, with `gpt-5.6-luna` for parsing |
| Judge | `gpt-6.1-sol` on an OpenAI Codex subscription, called through the Pi SDK |
| AACR source | `ee9d7fef70ef31acea2ff7ae316ce0a9575fe4df` |
| AACR input SHA-256 | `02c8561f1c3994a26f427d479b2a3f6e6c8e6858161fbc903b45248fa2cb2097` |
| Harbor | 0.24.0, `6099b8cf2bfbbb3e7ff1eb2163bfc4524dbd9ba5` |
| Harbor job | `-k 1 -n 2 -r 0`, data capture on |

Job evidence is stored locally rather than in this repository, under
`jobs/aacr-baseline-20261101-a` and `jobs/aacr-baseline-20261101-electron-a`.
