# AACR

Measures Daydream's committed review, its only reviewer since 0.35.0, against
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

Install the suite with the [quick start](../../README.md#quick-start), or just
`make setup`. It uses these pins:

| Input | Pin |
|---|---|
| AACR source | `ee9d7fef70ef31acea2ff7ae316ce0a9575fe4df` |
| Harbor | `6099b8cf2bfbbb3e7ff1eb2163bfc4524dbd9ba5` (0.24.0), set in the [Makefile](../../Makefile) |
| Daydream | see [`build.json`](../daydream/build.json), shared by every benchmark |

### Logins

`make quickstart` sets them to where Codex and Pi keep their logins unless you do.
When you run Harbor yourself, as below, export them, pointing at your normal login
folders. Don't copy `auth.json`: a copy clones its refresh token, so the copy and the
original would rotate it out from under each other.

```sh
export OPENROUTER_API_KEY=...             # The default reviewer slot: Daydream on Pi through OpenRouter
export REVIEWER_CODEX_AUTH_DIR=~/.codex   # A Codex reviewer slot
export JUDGE_PI_AUTH_DIR=~/.pi/agent      # Judge; Pi's /login, Sign in with ChatGPT
```

Neither container sees the rest of these folders, and logins stay out of task
assets and results:

- **Pi reviewer:** nothing is mounted. Harbor sets `OPENROUTER_API_KEY` on the agent's
  setup and run commands only, from `env` in the experiment, and the entrypoint hands it to
  Pi alone, with an empty Pi agent folder and no update checks or telemetry. A job's
  saved configuration holds `${OPENROUTER_API_KEY}`, not the key. The review
  container reaches only `openrouter.ai` on ReviewBench, and the public network on
  AACR, so a hostile review case could try to send the key elsewhere.
- **Codex reviewer:** only the Codex `auth.json` is mounted, and it stays writable, so
  Codex saves a refresh made during the review to your file. Codex must keep its
  login in `auth.json`, not in the system keyring. The review container has network
  access while it holds your Codex session, so a hostile review case could try to
  send that session elsewhere.
- **Judge:** nothing is mounted. Before scoring, the verifier refreshes Pi's
  `openai` (ChatGPT subscription) login on your machine with the pinned SDK, under Pi's own lock, when
  less than 30 minutes remain. It then uploads a copy holding only that login's
  access token, without the refresh token. If the refresh fails, the trial stops
  before scoring. If you raise the verifier timeout past about 20 minutes, set
  `min_judge_validity_sec` under `verifier.kwargs` to the new timeout plus 10
  minutes; otherwise the judge's token can expire mid-scoring and the trial fails.
  That login's tokens last about an hour and the verifier accepts at most 50 minutes
  here, so a verifier timeout can't go past 40 minutes. The login shares Pi's `openai` slot with an OpenAI API key, which it
  replaces.

Catalogs generated before the judge moved to this login (November 2026) still name
the deprecated `openai-codex` login, and their trials fail at scoring: generate them
again.

## Generate a catalog

```sh
make aacr-source   # the pinned AACR-Bench, in .cache/src/aacr-bench
.venv/bin/python -m benchmarks.aacr.convert \
  --upstream .cache/src/aacr-bench \
  --output artifacts/aacr/<fresh-catalog>
```

Every task gets the Daydream build from the quick start. Use a new output directory
each time; the converter won't overwrite one. A full
catalog takes about 46 GB. Add `--task <case>` (repeatable) to generate only
selected cases.

To tune Daydream without fitting it to every case, add `--split dev` for the cases to
tune on, and `--split holdout` only to confirm a change on the 59 cases listed in
[`holdout.txt`](holdout.txt), which tuning never sees. The default, `--split full`,
generates all 196. `--task` must name a case in the chosen split.

Eleven pinned commits no longer exist upstream, so a full conversion generates 185
of 196 cases and **exits 1**. That is expected. `catalog.json` records every case
and its error. Five of the eleven are held out, so `dev` generates 131 of 137 cases
and `holdout` 54 of 59, and both also exit 1.

## Run

Run from the repository root. [`experiments/aacr-baseline.yaml`](../../experiments/aacr-baseline.yaml)
holds the shared settings: Daydream's default review profile, the protected
verifier, the metric, data capture and zero retries.

```sh
.venv/bin/harbor run -c experiments/aacr-baseline.yaml -p artifacts/aacr/<fresh-catalog> \
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
.venv/bin/python -m benchmarks.aacr.convert --upstream .cache/src/aacr-bench --oracle
.venv/bin/harbor run -c experiments/aacr-oracle.yaml
```

## Rerun failed cases

Rerun in a **fresh named job**, selecting the cases you want:

```sh
.venv/bin/harbor run -c experiments/aacr-baseline.yaml -p artifacts/aacr/<fresh-catalog> \
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

## Compare builds over rounds

To compare the pinned build with a candidate, score each over several rounds. A round
is its own job, one pass over the catalog with `-k 1`, because Harbor pools the trials
of one job. AACR's match rate divides by every finding sent, so a build that floods
findings loses here even where ReviewBench's grounded precision doesn't show it.

**1. Build the candidate and generate one catalog per build**, from this revision of
the suite. Use `--split dev`; the held-out split is only for confirming a change.

```sh
.venv/bin/python -m benchmarks.daydream.build <daydream-clone> --candidate <commit>
.venv/bin/python -m benchmarks.aacr.convert --upstream .cache/src/aacr-bench --split dev \
  --task <case> --output artifacts/aacr/<pinned-catalog>
.venv/bin/python -m benchmarks.aacr.convert --upstream .cache/src/aacr-bench --split dev \
  --task <case> --build .cache/daydream-candidates/<commit>/build.json --output artifacts/aacr/<candidate-catalog>
```

**2. Run every round of every side as a fresh job**, as in [Run](#run), keeping `-k 1`
and `-r 0` and giving every job its own name.

**3. Report every side's jobs, in round order:**

```sh
.venv/bin/python -m benchmarks.aacr.report --catalog artifacts/aacr/<pinned-catalog> \
  --side jobs/<pinned-1> jobs/<pinned-2> jobs/<pinned-3> \
  --side jobs/<candidate-1> jobs/<candidate-2> jobs/<candidate-3> > <report>.json
```

[`loop/rounds.py`](../../loop/rounds.py) does steps 1 and 2 for one build and writes the
same scores and counts to `artifacts/loop/<name>/summary.json`, with each case's four
reward counts per round. It resumes a run that stopped, and a case that fails to convert
leaves no catalog behind, so drop it from the list and run again. It uses only the dev
split. The loop that tunes Daydream scores only ReviewBench's dev cases; AACR dev rounds
exist to check this report and a build. Held-out rounds belong to confirmation (#54).

```sh
.venv/bin/python -m loop.rounds --benchmark aacr --build pinned \
  --cases <case-list> --rounds 2 --name <fresh-name>
```

### What the report holds

[`report.py`](report.py) prints one JSON object. It reads jobs and checks sides with the
same code as [ReviewBench's report](../reviewbench/README.md#what-the-report-holds), so a
side is named from the Daydream commit its trials recorded, never from a label, and it
refuses:

- A side whose jobs mix builds, or hold a scored trial that left no review record.
- Two sides that are the same, and a job listed twice.
- A job with two trials for one review case, or a trial for a case outside the
  catalog. A case that failed to convert has no task, so it is outside.

Each round lists its trials and the same counts as ReviewBench's report: `expected`,
`missing`, `produced`, `completed_empty`, `incomplete`, `failed`, `evaluated`,
`elapsed_seconds`, and `cost_usd`, the API-equivalent estimates of the `cost_trials`
trials that reported one, null when none did. A trial is scored by the
same rule: not if it raised, never finished, was verified outside a separate
environment or had a failed step. Such a trial is dropped even if it left a reward, so a
round's metric here can differ from Harbor's own pooled job metric, shown in the same
round under `harbor`.

Scores come in two blocks, each naming its review cases under `blocks`:

- **`all`**: every review case in the catalog.
- **`matched`**: a reduced set, only the review cases that every job of every side
  scored.

For each block, `rounds` holds every round's result from [`metric.py`](metric.py)'s
`compute` over that round's rewards: `file_semantic_match_rate`,
`file_semantic_recall_rate`, `balanced_score` and their pooled counts, where
`generated_notes` is the findings sent. As in the job's own metric, an unscored trial
leaves the denominators rather than withholding the round, so read the scores with
`missing_instances`. A round with no scored trial defines no rate; one whose scored reviews sent no finding has a match rate of 0, as in AACR's own summary. `summary` holds each
rate's mean and sample standard deviation (n-1) over the rounds that define it, as
`loop/rounds.py` reports them; ReviewBench's report uses ReviewBench's own population
deviation. The report adds no score, weight or threshold, and doesn't rank the sides.

## Inspect results

```sh
.venv/bin/harbor view jobs
```

The viewer shows jobs, trials, scores, trajectories and artifacts. On disk, under
`jobs/<job>/`:

- `result.json` holds completion counts, errors and the pooled metric.
- `<trial>/result.json` and `<trial>/exception.txt` hold the trial's outcome.
- `<trial>/agent/execution.json` holds Daydream's terminal state, usage and retained
  findings. `daydream.stdout.log` shows why a review ended incomplete.
- `<trial>/agent/daydream/runs/<run>/deep/committed-review.json` is the sealed review:
  every claim, its source references and the settings it ran under.
- `<trial>/agent/dataset/runs/*.jsonl` holds Daydream's captured run record.
- `<trial>/verifier/` holds rewards, verdicts and full judge responses.

Monetary figures are API-equivalent estimates, not subscription charges.

## Attribution

[`upstream/`](upstream/) is AACR-Bench's evaluator, used under the
[Apache-2.0 license](../../LICENSE) with the changes listed in
[Citing this suite](../../docs/citing.md#where-the-code-comes-from). Generated
repository contexts keep their own licenses.
