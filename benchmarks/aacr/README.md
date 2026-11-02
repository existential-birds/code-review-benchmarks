# AACR

Measures Daydream's complete review pipeline against
[Alibaba AACR-Bench](https://github.com/alibaba/aacr-bench), a catalog of 196
pull requests with 1,506 human-annotated review comments. Each pull request
becomes a [Harbor](https://github.com/harbor-framework/harbor) task: Daydream
reviews the change in a sandbox, then a separate judge decides which of its
findings match the annotated comments.

Latest results are in [the baseline report](../../docs/aacr-baseline.md).

## What the scores mean

Scores measure agreement with AACR's annotated comments, not verified bug-finding
accuracy. A finding that matches no comment may still be a real problem.

- **Precision:** the share of Daydream's findings that match an annotated comment.
- **Recall:** the share of annotated comments covered by a finding.
- **Balanced score:** AACR's combined score, computed from those two.

[`metric.py`](metric.py) pools raw counts across cases and calls AACR's unchanged
`_compute_summary`, so these are pooled ratios, not averages of per-case ratios.

A review must complete to be scored. If any part of it ends incomplete, the trial
is recorded as an error and its references leave the denominators, so always read
scores alongside the failure count. Complete reviews with no findings are valid.

## Setup

Runs use pinned inputs:

| Input | Pin |
|---|---|
| AACR source | `ee9d7fef70ef31acea2ff7ae316ce0a9575fe4df` |
| Harbor | `6099b8cf2bfbbb3e7ff1eb2163bfc4524dbd9ba5` (0.24.0) |
| Daydream wheel | see [`daydream-build.json`](daydream-build.json) |

You need Docker, Git, Git LFS, `uv`, Python 3.14 for conversion, and an
environment with the pinned Harbor. Examples run `harbor` from that environment.

Build the wheel from the pinned Daydream source, then install Daydream's locked
runtime, the pinned Harbor, the wheel and this suite, in that order:

```sh
mkdir -p .cache/daydream-source
git -C /path/to/daydream archive <source-revision> | tar -x -C .cache/daydream-source
(cd .cache/daydream-source && uv build --wheel --out-dir ../daydream-wheel)

uv pip install --python .venv/bin/python --require-hashes \
  -r .cache/daydream-source/daydream/benchmark/harbor/runtime-requirements.lock
uv pip install --python .venv/bin/python -e /path/to/pinned/harbor
uv pip install --python .venv/bin/python --no-deps --reinstall \
  .cache/daydream-wheel/daydream-0.30.0-py3-none-any.whl
uv pip install --python .venv/bin/python --no-deps -e .
```

Review and judging use separate subscription logins, kept outside task assets and
results. The directories must stay writable so tokens can refresh:

```sh
export AACR_CODEX_AUTH_DIR=/path/to/codex-login   # Daydream reviewer
export AACR_PI_AUTH_DIR=/path/to/pi-login         # Judge; use Pi's /login for OpenAI Codex
```

## Generate a catalog

```sh
.venv/bin/python -m benchmarks.aacr.convert \
  --upstream /path/to/clean/pinned/aacr-bench \
  --output artifacts/aacr/<fresh-catalog> \
  --daydream-wheel .cache/daydream-wheel/daydream-0.30.0-py3-none-any.whl
```

Use a new output directory each time; the converter won't overwrite one. A full
catalog takes about 46 GB. Add `--task <case>` (repeatable) to generate only
selected cases.

Eleven pinned commits no longer exist upstream, so a full conversion generates 185
of 196 cases and **exits 1**. That is expected. `catalog.json` records every case
and its error.

## Run

Run from the repository root. [`experiments/aacr-baseline.yaml`](../../experiments/aacr-baseline.yaml)
holds the shared settings: Daydream's default review configuration, the protected
verifier, the metric, data capture and zero retries.

```sh
harbor run -c experiments/aacr-baseline.yaml -p artifacts/aacr/<fresh-catalog> \
  -k 1 -n 2 -r 0 --job-name <fresh-name>
```

Add `--dry-run` or `--print-config` first to check the plan. A full run at `-n 2`
took under 8 hours.

| To change | Use |
|---|---|
| Select cases | `-i <name>`, repeatable, globs allowed. Requires `-p` |
| Exclude cases | `-x <name>`, repeatable, globs allowed |
| Attempts per case | `-k <count>` |
| Concurrency | `-n <count>` |
| Retries | `-r <count>`; keep at 0, see below |
| Timeouts | `--agent-timeout-multiplier`, `--verifier-timeout-multiplier`, `--environment-build-timeout-multiplier` |

Default limits are 5,400 seconds per review, 300 for verification and 1,200 per
environment build. `experiments/aacr-pilot.yaml` selects a ten-case, one-per-language
subset of a catalog at `artifacts/aacr/tasks`, the converter's default output. Pass it
as a second `-c` in place of `-p`.

## Rerun failed cases

Rerun in a **fresh named job**, selecting the cases you want:

```sh
harbor run -c experiments/aacr-baseline.yaml -p artifacts/aacr/<fresh-catalog> \
  -i <case> -i <other-case> -k 1 -n 2 -r 0 --job-name <fresh-rerun-name>
```

Avoid these if you want to keep what the failed run left behind:

- **`harbor jobs resume`** with an error filter deletes the matching failed trials.
- **Reopening an unfinished job** can delete trials that have no `result.json`.
- **Automatic retries** delete each failed attempt's directory before retrying,
  which is why `-r 0` is the default here.

A rerun's scores aren't merged into the original job's pooled score. A malformed
reward or a failing metric subprocess ends the whole Harbor job; rerun the full
selection in a fresh job to keep one pooled score.

## Inspect results

```sh
harbor view jobs
```

The viewer shows jobs, trials, scores, trajectories and artifacts. On disk, under
`jobs/<job>/`:

- `result.json` holds completion counts, errors and the pooled metric.
- `<trial>/result.json` and `<trial>/exception.txt` hold the trial's outcome.
- `<trial>/agent/execution.json` holds Daydream's terminal state, usage and retained
  findings. `daydream.stdout.log` shows why a review ended incomplete.
- `<trial>/agent/dataset/runs/*.jsonl` holds Daydream's captured run record.
- `<trial>/verifier/` holds rewards, verdicts and full judge responses.

Monetary figures are API-equivalent estimates, not subscription charges.

## Attribution

The vendored evaluator in [`upstream/`](upstream/) comes from
[Alibaba AACR-Bench](https://github.com/alibaba/aacr-bench) under the root
[Apache-2.0 license](../../LICENSE). Generated repository contexts keep their own
licenses. One local change: the converter rejects an existing raw-data file whose
SHA-256 differs from its metadata when no download URL is available.

See [Citing this suite](../../docs/citing.md) for AACR-Bench's citation and how this
integration differs from upstream.
