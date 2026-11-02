# Citing this suite

If you use a result or implementation from this suite, cite both the original
benchmark and the exact version of this suite. The benchmark defines the cases and
scoring; this suite defines how they were run.

## Cite the benchmark

AACR-Bench [asks for this paper](https://github.com/alibaba/aacr-bench#-citation):

```bibtex
@misc{zhang2026aacrbenchevaluatingautomaticcode,
  title={AACR-Bench: Evaluating Automatic Code Review with Holistic Repository-Level Context},
  author={Lei Zhang and Yongda Yu and Minghui Yu and Xinxin Guo and Zhengqi Zhuang and Guoping Rong and Dong Shao and Haifeng Shen and Hongyu Kuang and Zhengfeng Li and Boge Wang and Guoan Zhang and Bangyu Xiang and Xiaobin Xu},
  year={2026},
  eprint={2601.19494},
  archivePrefix={arXiv},
  primaryClass={cs.SE},
  url={https://arxiv.org/abs/2601.19494},
}
```

## Cite this suite

Identify the exact release or commit, because conversion and scoring can change
between versions. There is no release yet, so give the full commit SHA:

```text
Kevin Anderson. Code Review Benchmarks, commit <full-sha>.
https://github.com/existential-birds/code-review-benchmarks
```

[`CITATION.cff`](../CITATION.cff) holds the same details in a standard format.

## Where the code comes from

| Material | Origin | License |
|---|---|---|
| [`benchmarks/aacr/upstream/`](../benchmarks/aacr/upstream/) | AACR-Bench `evaluation/`, with changes listed below | Apache-2.0 |
| AACR review cases and annotations | AACR-Bench, read at conversion and not stored here | AACR-Bench's license |
| Repository contexts in generated tasks | Each reviewed project, not stored here | Each project's license |
| Everything else in this repository | This suite | [Apache-2.0](../LICENSE) |

The imported evaluator differs from AACR's source in two ways. Chinese comments and
messages are translated to English, and the converter rejects an existing raw-data
file whose SHA-256 differs from its metadata when no download URL is available.
The scoring logic and judge prompts are unchanged.

## AACR inputs

| Input | Value |
|---|---|
| Dataset and evaluator | [`alibaba/aacr-bench`](https://github.com/alibaba/aacr-bench) at `ee9d7fef70ef31acea2ff7ae316ce0a9575fe4df` |
| Input file | `positive_samples.json`, SHA-256 `02c8561f1c3994a26f427d479b2a3f6e6c8e6858161fbc903b45248fa2cb2097` |
| Selected review cases | All 196 distinct pull requests in that file, with 1,506 reference findings |
| Runnable review cases | 185 |
| Pilot review cases | The ten listed in [`experiments/aacr-pilot.yaml`](../experiments/aacr-pilot.yaml) |

AACR-Bench describes 200 pull requests; this input file contains 196. The separate
`negative_samples.json` file isn't used.

Eleven review cases can't run because their pinned commits no longer exist upstream.
Together they hold 87 reference findings:

- `astral-sh-uv-11088`
- `clickhouse-clickhouse-74070`
- `clickhouse-clickhouse-85266`
- `comfyanonymous-comfyui-7952`
- `comfyanonymous-comfyui-9560`
- `keycloak-keycloak-35645`
- `keycloak-keycloak-36457`
- `keycloak-keycloak-37465`
- `keycloak-keycloak-37504`
- `keycloak-keycloak-41672`
- `nodejs-node-56185`

## How this differs from AACR-Bench

| Area | AACR-Bench | This suite |
|---|---|---|
| Execution | AACR's own pipeline | One [Harbor](https://github.com/harbor-framework/harbor) task per pull request, named from its URL |
| Reviewer | OpenCodeReview, Claude Code or Codex | Daydream's complete review pipeline. Findings use AACR's Codex result shape, and file-level findings without a line are kept |
| Judge | Any model behind an OpenAI-compatible API, set by `JUDGE_MODEL` | `openai-codex/gpt-6.1-sol` through the Pi SDK 1.4.0, on a subscription login |
| Judge request | `temperature=0`, `top_p=0.95`, `max_tokens=3000`, thinking off | None of those are sent; the provider's defaults apply |
| Judge failure | Logged and counted as no match | The trial fails with no score |
| Missing API key | Falls back to local mock matching | Mock matching is disabled |
| Incomplete review | Only a missing result file is excluded from metrics | The trial fails with no score, even if findings were retained |
| Metrics | File-level semantic metrics by default | The same metrics through AACR's `_compute_summary`, pooled across scored trials by [`metric.py`](../benchmarks/aacr/metric.py) |

## Score equivalence

No equivalence with AACR-Bench's published scores has been validated. These scores
haven't been compared with AACR's results or with its own pipeline on the same
findings, and the different judge can change which findings match.

Compare scores only between runs of this suite that use the same review cases and
scoring settings. The checked claim is narrower: the imported evaluator matches the
pinned AACR source apart from the changes listed above.
