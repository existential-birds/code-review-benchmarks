# AACR

Measures Daydream's complete review pipeline against
[Alibaba AACR-Bench](https://github.com/alibaba/aacr-bench), a catalog of 196
pull requests with 1,506 human-annotated review comments. Each pull request
becomes a [Harbor](https://github.com/harbor-framework/harbor) task: Daydream
reviews the change in a sandbox, then a separate judge decides which of its
findings match the annotated comments.

## What the scores mean

Scores measure agreement with AACR's annotated comments, not verified bug-finding
accuracy. A finding that matches no comment may still be a real problem.

A finding counts toward precision only if it matches a comment no earlier finding
covered, so duplicates lower precision. The balanced score is the F1 of precision
and recall, each rounded to three places first. [`metric.py`](metric.py) pools raw
counts across cases and calls AACR's unchanged `_compute_summary`, so all three
are pooled ratios, not averages of per-case ratios.

A review must complete to be scored. If any part of it ends incomplete, the trial
is recorded as an error and its references leave the denominators, so always read
scores alongside the failure count. Complete reviews with no findings are valid.

## Setup

Install the suite with the [quick start](../../README.md#quick-start). It uses
these pins:

| Input | Pin |
|---|---|
| AACR source | `ee9d7fef70ef31acea2ff7ae316ce0a9575fe4df` |
| Harbor | `6099b8cf2bfbbb3e7ff1eb2163bfc4524dbd9ba5` (0.24.0) |
| Daydream wheel | see [`daydream-build.json`](daydream-build.json) |

Examples below run `harbor` from the suite's `.venv`.

### Logins

Point both variables at your normal login folders. Don't copy `auth.json`: a copy
clones its refresh token, so the copy and the original would rotate it out from
under each other.

```sh
export AACR_CODEX_AUTH_DIR=~/.codex      # Daydream reviewer
export AACR_PI_AUTH_DIR=~/.pi/agent      # Judge; use Pi's /login for OpenAI Codex
```

Neither container sees the rest of these folders, and logins stay out of task
assets and results:

- **Reviewer:** only the Codex `auth.json` is mounted, and it stays writable, so
  Codex saves a refresh made during the review to your file. Codex must keep its
  login in `auth.json`, not in the system keyring. The review container has network
  access while it holds your Codex session, so a hostile review case could try to
  send that session elsewhere.
- **Judge:** nothing is mounted. Before scoring, the verifier refreshes Pi's
  `openai-codex` login on your machine with the pinned SDK, under Pi's own lock, when
  less than 30 minutes remain. It then uploads a copy holding only that login's
  access token, without the refresh token. If the refresh fails, the trial stops
  before scoring. If you raise the verifier timeout past about 20 minutes, set
  `min_judge_validity_sec` under `verifier.kwargs` to the new timeout plus 10
  minutes; otherwise the judge's token can expire mid-scoring and the trial fails.

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

Add `--dry-run` or `--print-config` first to check the plan. A full run of
Daydream 0.30.0 at `-n 2` took under 8 hours and about 393 million reviewer input
tokens, most of them cached: roughly $290 at API prices.

| To change | Use |
|---|---|
| Select cases | `-i <name>`, repeatable, globs allowed, with `-p` |
| Exclude cases | `-x <name>`, repeatable, globs allowed, with `-p` |
| Attempts per case | `-k <count>` |
| Concurrency | `-n <count>` |
| Retries | `-r <count>`; keep at 0, see below |
| Timeouts | `--agent-timeout-multiplier`, `--verifier-timeout-multiplier`, `--environment-build-timeout-multiplier` |

Default limits are 5,400 seconds per review, 300 for verification and 1,200 per
environment build. `experiments/aacr-pilot.yaml` selects a ten-case, one-per-language
subset of a catalog at `artifacts/aacr/tasks`, the converter's default output. Pass it
as a second `-c` in place of `-p`.

To check the judge and scoring without running Daydream, generate the Laravel
oracle case, which submits a fixed finding, and run it:

```sh
.venv/bin/python -m benchmarks.aacr.convert --upstream /path/to/clean/pinned/aacr-bench --oracle
harbor run -c experiments/aacr-oracle.yaml
```

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

[`upstream/`](upstream/) is AACR-Bench's evaluator, used under the
[Apache-2.0 license](../../LICENSE) with the changes listed in
[Citing this suite](../../docs/citing.md#where-the-code-comes-from). Generated
repository contexts keep their own licenses.
