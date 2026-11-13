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

ReviewBench, developed by GitHub, has no paper; cite its repository at the pinned
commit:

```text
ReviewBench. https://github.com/review-bench/ReviewBench, commit 4f04392d6bf4941ab6f8a3ea0c15b449f4458864.
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
| [`benchmarks/reviewbench/upstream/metric.py`](../benchmarks/reviewbench/upstream/metric.py) | ReviewBench `scripts/harbor/metric.py`, unchanged | [MIT](../benchmarks/reviewbench/upstream/LICENSE) |
| ReviewBench tasks, golden findings and judge | ReviewBench's exporter, run at conversion and not stored here | [MIT](../benchmarks/reviewbench/upstream/LICENSE) |
| Repository contexts in generated tasks | Each reviewed project, not stored here | Each project's license |
| [`.agents/skills/test-audit/`](../.agents/skills/test-audit/) | Adapted in part from [OpenClaw](https://github.com/openclaw/openclaw)'s test-audit guidance | [MIT](../.agents/skills/test-audit/LICENSE) |
| Everything else in this repository | This suite | [Apache-2.0](../LICENSE) |

Only AACR's evaluator modules are imported; its reviewers, pipeline, hooks and MCP
servers are not. They differ from AACR's source in three ways:

- Chinese comments, docstrings and messages are translated to English, including
  the judge-failure reason saved as `judge_reason`.
- The converter rejects an existing raw-data file whose SHA-256 differs from its
  metadata when no download URL is available.
- A new [`__init__.py`](../benchmarks/aacr/upstream/__init__.py) makes AACR's flat
  imports, such as `import judge`, resolve to the package's modules.

The scoring logic, judge prompts and data field names are unchanged.

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
| Judge | Any model behind an OpenAI-compatible API, set by `JUDGE_MODEL` | `gpt-6.1-sol` on an OpenAI Codex subscription, called through the Pi SDK 1.5.0 |
| Judge request | `temperature=0`, `top_p=0.95`, `max_tokens=3000`, thinking off | None of those are sent; the provider's defaults apply |
| Judge failure | Logged and counted as no match | The trial fails with no score |
| Missing API key | Falls back to local mock matching | Mock matching is disabled |
| Incomplete review | Only a missing result file is excluded from metrics | The trial fails with no score, even if findings were retained |
| Metrics | File-level semantic metrics by default | The same metrics through AACR's `_compute_summary`, pooled across scored trials by [`metric.py`](../benchmarks/aacr/metric.py) |

## ReviewBench inputs

| Input | Value |
|---|---|
| Tasks, judge and golden findings | [`review-bench/ReviewBench`](https://github.com/review-bench/ReviewBench) at `4f04392d6bf4941ab6f8a3ea0c15b449f4458864` |
| Review cases | The 219-PR full set, or its 25-PR test set with `--split test` |
| Repository contexts | ReviewBench's `review-bench/<owner>_<repo>` GitHub mirrors, fetched when each task image builds |

## How this differs from ReviewBench

| Area | ReviewBench | This suite |
|---|---|---|
| Tasks | Written by its `scripts/harbor/export.ts` | The same exporter's tasks, with Daydream in the review image and the judge pointed at a Pi login |
| Reviewer | Any agent that writes its findings file | Daydream's complete review pipeline. Findings without a line in the head are left out and counted |
| Review network | Public model API hosts | `chatgpt.com` and `auth.openai.com`, for Codex's subscription login |
| Review timeout | 900 seconds | 5,400 seconds |
| Judge | Claude Opus 5 through `api.anthropic.com`, for the leaderboard and the public Harbor dataset | `gpt-6.1-sol` on an OpenAI Codex subscription, through ReviewBench's own Pi SDK 0.75.5 |
| Matching, classification and prompts | — | Unchanged |
| Incomplete runs | Every score is withheld unless all planned trials scored | Scored trials are pooled and missing ones counted, by [`metric.py`](../benchmarks/reviewbench/metric.py) |
| Repeated attempts | Independent rounds, reported as means and standard deviations | Pooled with the job's other trials. Rounds are separate jobs, scored by ReviewBench's round metric in [`report.py`](../benchmarks/reviewbench/report.py) |

## Score equivalence

Scores here aren't comparable with either benchmark's published results, and no
run has been checked against them. ReviewBench's leaderboard and public dataset use
a Claude Opus 5 judge, AACR-Bench's results come from its own pipeline, and a
different judge can change which findings match.

Compare scores only between runs of this suite with the same review cases and
scoring settings. The one checked claim is that the imported evaluator matches the
pinned AACR source apart from the changes listed above.
