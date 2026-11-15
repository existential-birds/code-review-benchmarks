# ReviewBench

Measures Daydream's complete review pipeline against
[ReviewBench](https://github.com/review-bench/ReviewBench): 219 real pull requests,
each with a human-reviewed set of golden findings, plus a 25-PR test set drawn from
them. ReviewBench's own exporter writes each pull request as a
[Harbor](https://github.com/harbor-framework/harbor) task. Daydream reviews it in a
sandbox, then ReviewBench's unchanged judge matches the findings against the golden
set in a separate verifier.

## What the scores mean

ReviewBench reports four ratios. [`metric.py`](metric.py) pools the raw counts of
every scored trial and applies ReviewBench's own formulas from
[`upstream/metric.py`](upstream/metric.py):

- **Grounded precision**: matched findings that hit a true golden finding, out of
  all matched findings.
- **Grounded recall**: golden true findings covered, out of all golden true findings.
- **Augmented precision and recall**: the same, also crediting findings that match
  nothing but the judge's classifier rates as valid.
- **F1**: the harmonic mean of each pooled precision and recall pair.

The metric also breaks the four ratios down by golden severity. Each trial's reward
keeps the category breakdown and the per-PR counts.

A review must complete to be scored. A failed trial adds to `missing_trials` and
leaves the pooled counts, so read scores alongside it. A complete review with no
findings is scored. ReviewBench's own metric withholds every score unless all
planned trials scored; this one reports the scored trials instead. Repeated attempts
(`-k`) pool together instead of forming ReviewBench's separate rounds; to score
rounds, [run each as its own job](#compare-builds-over-rounds).

**These scores aren't comparable with ReviewBench's leaderboard or its public Harbor
dataset**, both of which use a Claude Opus 5 judge. Here the judge is `gpt-6.1-sol`
on an OpenAI Codex subscription, so compare only runs of this suite. See
[how this differs](../../docs/citing.md#how-this-differs-from-reviewbench).

ReviewBench wants a line in the pull request's head for every finding. Daydream
findings about a whole file have none, so they're left out and counted as
`omitted_findings` in each trial's `agent/execution.json` and metadata. So is a
finding whose only location is on the old side of the change: Daydream places it on
the file, since a line the pull request removed isn't in the head.

The pinned reviewer is Daydream's committed review. Each finding is sent as its title,
then its claim, trigger, consequence and supporting grounds, labelled as in Daydream's
own report. ReviewBench's findings have no severity field, so the severity Daydream
rates each finding with stays in `execution.json`'s `retained_findings`.

## Setup

Install the suite with the [quick start](../../README.md#quick-start), then clone
ReviewBench at its pin and install its exporter's dependencies:

```sh
git clone https://github.com/review-bench/ReviewBench ../ReviewBench
git -C ../ReviewBench checkout 4f04392d6bf4941ab6f8a3ea0c15b449f4458864
npm ci --prefix ../ReviewBench
```

Set the two login variables from [the AACR guide](../aacr/README.md#logins); both
benchmarks use them the same way. ReviewBench's tasks fence the network more tightly
than AACR's. The review can reach only `chatgpt.com` and `auth.openai.com`, and the
judge only `chatgpt.com`, which needs Docker with Harbor's network isolation (Docker
Desktop works).

## Generate a catalog

```sh
.venv/bin/python -m benchmarks.reviewbench.convert \
  --upstream ../ReviewBench --output artifacts/reviewbench/<fresh-catalog>
```

This writes all 219 tasks. To tune Daydream, use `--split dev` for the 194 PRs
outside the test set, and `--split test` for ReviewBench's 25-PR test set only to
confirm a change, since tuning never sees it. Add `--task <name>` (repeatable) to
generate only selected tasks from the chosen split. Use a new output directory each
time. The catalog is small: each task's image fetches its pull request from
ReviewBench's GitHub mirror when Harbor first builds it.

## Run

```sh
.venv/bin/harbor run -c experiments/reviewbench.yaml -p artifacts/reviewbench/<fresh-catalog> \
  -k 1 -n 2 -r 0 --job-name <fresh-name>
```

[`experiments/reviewbench.yaml`](../../experiments/reviewbench.yaml) holds the shared
settings. It gives each review 5,400 seconds instead of ReviewBench's 900, and
4 CPUs and 8 GB, as AACR does. Selecting cases, attempts, concurrency, retries,
reruns and timeouts all work as in [the AACR guide](../aacr/README.md#run), including
its advice on keeping failed trials.

To check the judge without running Daydream, score ReviewBench's own true findings
for one task:

```sh
.venv/bin/harbor run -c experiments/reviewbench-oracle.yaml \
  -p artifacts/reviewbench/<fresh-catalog> -l 1
```

## Compare builds over rounds

The steps below compare the pinned build with a candidate build over several rounds
of ReviewBench's test set. A round is its own job, because Harbor pools the trials of
one job.

**1. Build the candidate and generate one catalog per build.**

```sh
.venv/bin/python -m benchmarks.daydream.build ../daydream --candidate <commit>
.venv/bin/python -m benchmarks.reviewbench.convert --upstream ../ReviewBench --split test \
  --output artifacts/reviewbench/<pinned-catalog>
.venv/bin/python -m benchmarks.reviewbench.convert --upstream ../ReviewBench --split test \
  --build .cache/daydream-candidates/<commit>/build.json --output artifacts/reviewbench/<candidate-catalog>
```

Generate both catalogs from this revision of the suite, since every task image holds a
copy of the reviewer entrypoint.

**2. Run a pilot: one review case, one trial per side.**

```sh
.venv/bin/harbor run -c experiments/reviewbench.yaml -p artifacts/reviewbench/<pinned-catalog> \
  -i <case> -k 1 -n 1 -r 0 --job-name <pilot-pinned>
.venv/bin/harbor run -c experiments/reviewbench.yaml -p artifacts/reviewbench/<candidate-catalog> \
  -i <case> -k 1 -n 1 -r 0 --job-name <pilot-candidate>
```

Report the two jobs as in step 4, one `--side` each, and read each trial's
status, elapsed time, usage and cost estimate before the full run. The pilot's `all`
block is withheld, since the other review cases are missing; its one case is in
`matched`.

**3. Run every round of every side as a fresh job.** Drop `-i <case>` from the two
commands above, choose the concurrency with `-n`, and give every job its own name.
Keep `-k 1` and `-r 0`: a failed trial stays failed in its round.

**4. Report every side's jobs, in round order:**

```sh
.venv/bin/python -m benchmarks.reviewbench.report --catalog artifacts/reviewbench/<pinned-catalog> \
  --side jobs/<pinned-1> jobs/<pinned-2> jobs/<pinned-3> \
  --side jobs/<candidate-1> jobs/<candidate-2> jobs/<candidate-3> > <report>.json
```

### What a trial keeps

The review runs under Daydream's default profile, unchanged, and is given the pull
request's title and body as untrusted context, which the sealed result records in
`settings.historical_context`. Daydream refuses a title and body over 65,536 bytes,
measured as it encodes them for the reviewer, and that fails the trial.

`execution.json` records the profile's digest in
`resolved_configuration.review_profile`, and in `committed_review.assignment_policy`
how the sealed result assigned the review, including whether it `decomposed` into
assignments.

The evidence under `agent/daydream/runs/<run>/` is Daydream's artifact dump. Its
`deep/committed-review.json` is the sealed result: every claim, its source references
and the settings the review ran under. The run's trajectory is also copied to
`agent/trajectory.json`.

### What the report holds

[`report.py`](report.py) prints one JSON object. A side is named from what its
trials recorded, never from a label: `<Daydream commit>/review`, or
`<Daydream commit>/<policy>` for a job from a suite revision that had the
`assignment_policy` option. The report refuses:

- A side whose jobs mix builds or reviews, or hold a scored trial that left no review
  record, such as an oracle job's.
- Two sides that are the same, and a job listed twice.
- A job with two trials for one review case, or a trial for a case outside the
  catalog.

A job with fewer trials than the catalog has review cases is accepted, and the rest
count as `missing`.

Each round lists its trials and counts them:

| Count | Trials |
|---|---|
| `expected`, `missing` | Review cases in the catalog, and those the job has no trial for |
| `produced`, `completed_empty` | Complete reviews that sent findings, and those that sent none |
| `incomplete`, `failed` | Reviews that didn't finish their analysis, and every other trial |
| `evaluated` | Scored trials. By ReviewBench's own rule, a trial isn't scored if it raised, never finished, was verified outside a separate environment or had a failed step |
| `conversion_limited`, `omitted_findings` | Trials with a finding left out for want of a head line, and those findings |
| `decomposed` | Reviews whose sealed result split into assignments |

`elapsed_seconds` sums the review time the trials recorded. `cost_usd` sums the
API-equivalent estimates of the `cost_trials` trials that reported one, and is null
when none did; subscription charges are unknown. `harbor` holds Harbor's own totals
and pooled metric for the job. Harbor's own job cost stays empty: the agent gives
Harbor token counts and no cost, because the figure is an estimate and not a charge,
so the report sums the estimate from each trial's `execution.json`.

Scores are ReviewBench's unchanged round metric: every round, then the mean and
standard deviation over the rounds. They come in two blocks, each naming its
review cases under `blocks`:

- **`all`**: every review case in the catalog. As in ReviewBench, one unscored
  trial withholds its round's ratios and every average.
- **`matched`**: a reduced set, only the review cases that every job of every side
  scored.

The report adds no score, weight or threshold, and doesn't rank the sides.

## Inspect results

`harbor view jobs` shows each job. On disk, `jobs/<job>/result.json` holds the
pooled metric, and each trial keeps Daydream's evidence under `agent/` as for AACR.
The verifier's `scores.json` and `test-stdout.txt` hold ReviewBench's full per-PR
report: every match, classification and breakdown.

## Attribution

ReviewBench's exporter, judge and golden findings come from the pinned source when
a catalog is generated and aren't stored here. [`upstream/metric.py`](upstream/metric.py)
is ReviewBench's unchanged metric, used under its [MIT license](upstream/LICENSE).
