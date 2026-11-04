# Code Review Benchmarks

Measures how well [Daydream](https://github.com/existential-birds/daydream) reviews
code, so we can tell whether a new version or configuration is actually better.
Each pull request in a benchmark becomes a [Harbor](https://github.com/harbor-framework/harbor)
task: Daydream reviews it in a sandbox, then a separate judge matches the findings
against human-reviewed reference findings. The benchmarks are
[AACR-Bench](benchmarks/aacr/README.md) and [ReviewBench](benchmarks/reviewbench/README.md).

Scores are precision, recall and their F1, pooled across review cases. They aren't
comparable with either benchmark's published results, because the judge differs;
see [score equivalence](docs/citing.md#score-equivalence).

Every benchmark runs the one Daydream build pinned in
[`benchmarks/daydream/build.json`](benchmarks/daydream/build.json). To measure another
version, change that file, rebuild and regenerate the catalogs; Harbor refuses to run
a task generated for a different pin. To score an unreleased commit without changing
the pin, use a [candidate build](#score-an-unreleased-daydream-commit).

## Quick start

Requires Docker, Git LFS, [`uv`](https://docs.astral.sh/uv/), Node 22.19+, a Codex
login for the reviewer, and [Pi](https://shittycodingagent.ai/) logged in to an
OpenAI Codex subscription for the judge.

**1. Clone the pinned sources:**

```sh
git clone https://github.com/existential-birds/code-review-benchmarks
git clone https://github.com/harbor-framework/harbor
git clone https://github.com/existential-birds/daydream
git clone https://github.com/alibaba/aacr-bench

git -C harbor checkout 6099b8cf2bfbbb3e7ff1eb2163bfc4524dbd9ba5
git -C aacr-bench checkout ee9d7fef70ef31acea2ff7ae316ce0a9575fe4df
cd code-review-benchmarks
```

**2. Build the pinned Daydream and install.** The build step writes the wheel and
its dependency lock to `.cache/daydream` and rejects a wheel whose hash differs from
the pin.

```sh
uv venv --python 3.14 .venv
.venv/bin/python -m benchmarks.daydream.build ../daydream

uv pip install --python .venv/bin/python --require-hashes \
  -r .cache/daydream/runtime-requirements.lock
uv pip install --python .venv/bin/python -e ../harbor
uv pip install --python .venv/bin/python --no-deps --reinstall .cache/daydream/daydream-*.whl
uv pip install --python .venv/bin/python --no-deps -e .
npm ci --omit=dev --prefix benchmarks/pi   # Pi SDK; refreshes the judge's login on the host
```

**3. Point the suite at your login folders**, not copies
([what each container sees](benchmarks/aacr/README.md#logins)):

```sh
export REVIEWER_CODEX_AUTH_DIR=~/.codex
export JUDGE_PI_AUTH_DIR=~/.pi/agent
```

**4. Generate one review case:**

```sh
.venv/bin/python -m benchmarks.aacr.convert \
  --upstream ../aacr-bench \
  --output artifacts/aacr/quickstart \
  --task bluewave-labs-checkmate-1897
```

**5. Run it and open the viewer:**

```sh
.venv/bin/harbor run -c experiments/aacr-baseline.yaml -p artifacts/aacr/quickstart \
  -k 1 -n 1 -r 0 --job-name quickstart
.venv/bin/harbor view jobs
```

The [AACR guide](benchmarks/aacr/README.md) covers full runs, reruns and reading
results. The [ReviewBench guide](benchmarks/reviewbench/README.md) runs the same
reviewer and logins on ReviewBench's pull requests.

## Score an unreleased Daydream commit

Build the commit as a candidate, then generate a fresh catalog with the `build.json`
it prints. ReviewBench's converter takes the same `--build`. The catalog runs as
usual.

```sh
.venv/bin/python -m benchmarks.daydream.build ../daydream --candidate <commit>
.venv/bin/python -m benchmarks.aacr.convert --upstream ../aacr-bench \
  --output artifacts/aacr/<fresh-catalog> --build <printed build.json>
```

Published results use only the pin.
