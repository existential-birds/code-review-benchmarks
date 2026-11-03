# Code Review Benchmarks

Measures how well [Daydream](https://github.com/existential-birds/daydream) reviews
code, so we can tell whether a new version or configuration is actually better.
Runs use [Harbor](https://github.com/harbor-framework/harbor): each pull request
gets its own sandboxed review, then a separate judge scores the findings against
human-annotated review comments.

The first integrated benchmark is
[AACR-Bench](https://github.com/alibaba/aacr-bench).

## Quick start

This reviews one pull request and scores it. You need:

- Docker, Git and Git LFS
- [`uv`](https://docs.astral.sh/uv/) and Node 22.19 or later
- A Codex login for the reviewer
- [Pi](https://shittycodingagent.ai/) logged in to an OpenAI Codex subscription,
  for the judge

**1. Get the pinned sources.** The suite expects exact revisions of each project:

```sh
git clone https://github.com/existential-birds/code-review-benchmarks
git clone https://github.com/harbor-framework/harbor
git clone https://github.com/existential-birds/daydream
git clone https://github.com/alibaba/aacr-bench

git -C harbor checkout 6099b8cf2bfbbb3e7ff1eb2163bfc4524dbd9ba5
git -C aacr-bench checkout ee9d7fef70ef31acea2ff7ae316ce0a9575fe4df
cd code-review-benchmarks
```

**2. Build Daydream and install everything.** Conversion checks the wheel against
the hash in [`daydream-build.json`](benchmarks/aacr/daydream-build.json):

```sh
uv venv --python 3.14 .venv
mkdir -p .cache/daydream-source
git -C ../daydream archive 5bae1ac09918ecf8d095aaf6d283235ca1ad83a8 \
  | tar -x -C .cache/daydream-source
(cd .cache/daydream-source && uv build --wheel --out-dir ../daydream-wheel)

uv pip install --python .venv/bin/python --require-hashes \
  -r .cache/daydream-source/daydream/benchmark/harbor/runtime-requirements.lock
uv pip install --python .venv/bin/python -e ../harbor
uv pip install --python .venv/bin/python --no-deps --reinstall \
  .cache/daydream-wheel/daydream-0.30.0-py3-none-any.whl
uv pip install --python .venv/bin/python --no-deps -e .
npm ci --omit=dev --prefix benchmarks/aacr
```

The last command installs the pinned Pi SDK, which refreshes the judge's login on
your machine before scoring.

**3. Point the suite at your logins.** Use your normal login folders, not a copy:

```sh
export AACR_CODEX_AUTH_DIR=~/.codex
export AACR_PI_AUTH_DIR=~/.pi/agent
```

The reviewer sees only your Codex `auth.json`. The judge gets only Pi's
`openai-codex` login, refreshed on your machine first.

**4. Generate one review case:**

```sh
.venv/bin/python -m benchmarks.aacr.convert \
  --upstream ../aacr-bench \
  --output artifacts/aacr/quickstart \
  --daydream-wheel .cache/daydream-wheel/daydream-0.30.0-py3-none-any.whl \
  --task bluewave-labs-checkmate-1897
```

**5. Run it and look at the result:**

```sh
.venv/bin/harbor run -c experiments/aacr-baseline.yaml -p artifacts/aacr/quickstart \
  -k 1 -n 1 -r 0 --job-name quickstart
.venv/bin/harbor view jobs
```

See [the AACR guide](benchmarks/aacr/README.md) to run the full catalog, rerun failed
cases and read job output.

## How scoring works

- **Precision** is the share of findings that match an annotated comment.
- **Recall** is the share of annotated comments covered by a finding.
- **Balanced score** is AACR's combined score, computed from those two.

Scores pool raw counts across review cases. A review must finish to be scored, so
read scores alongside the failure count.

Only compare runs that use the same review cases and scoring settings. Scores here
aren't equivalent to those published by AACR-Bench; see
[how this differs from upstream](docs/citing.md#how-this-differs-from-aacr-bench).

## Repository layout

```text
benchmarks/aacr/  # AACR conversion, task templates, reviewer and judge adapters, scoring
experiments/      # Harbor job configurations
docs/             # Citation guide
tests/            # Python and Node tests
```

Harbor owns execution, from task selection to result storage; this repository adds
only what connects a benchmark to it. See [Harbor first](CONTRIBUTING.md#harbor-first).

## Contributing and citation

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. To cite results,
cite the original benchmark and the exact commit of this suite; see
[Citing this suite](docs/citing.md).

Licensed under [Apache-2.0](LICENSE).
