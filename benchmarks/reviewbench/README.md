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
(`-k`) pool together instead of forming ReviewBench's separate rounds.

**These scores aren't comparable with ReviewBench's leaderboard or its public Harbor
dataset**, both of which use a Claude Opus 5 judge. Here the judge is `gpt-6.1-sol`
on an OpenAI Codex subscription, so compare only runs of this suite. See
[how this differs](../../docs/citing.md#how-this-differs-from-reviewbench).

ReviewBench wants a line in the pull request's head for every finding. Daydream
findings about a whole file have none, so they're left out and counted as
`omitted_findings` in each trial's `agent/execution.json` and metadata.

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

## Inspect results

`harbor view jobs` shows each job. On disk, `jobs/<job>/result.json` holds the
pooled metric, and each trial keeps Daydream's evidence under `agent/` as for AACR.
The verifier's `scores.json` and `test-stdout.txt` hold ReviewBench's full per-PR
report: every match, classification and breakdown.

## Attribution

ReviewBench's exporter, judge and golden findings come from the pinned source when
a catalog is generated and aren't stored here. [`upstream/metric.py`](upstream/metric.py)
is ReviewBench's unchanged metric, used under its [MIT license](upstream/LICENSE).
