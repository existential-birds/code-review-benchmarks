# Code Review Benchmarks

Measures how well [Daydream](https://github.com/existential-birds/daydream) reviews
code, so we can tell whether a new version or configuration is actually better.
Each pull request in a benchmark becomes a [Harbor](https://github.com/harbor-framework/harbor)
task: Daydream reviews it in a sandbox, then a separate judge matches the findings
against human-annotated review comments. The first benchmark is
[AACR-Bench](https://github.com/alibaba/aacr-bench).

Scores are precision, recall and their F1, pooled across review cases. They aren't
comparable with AACR-Bench's published results; see
[how this suite differs](docs/citing.md#how-this-differs-from-aacr-bench).

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

**2. Build Daydream and install.** Conversion rejects a wheel whose hash differs
from [`daydream-build.json`](benchmarks/aacr/daydream-build.json).

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
npm ci --omit=dev --prefix benchmarks/aacr   # Pi SDK; refreshes the judge's login on the host
```

**3. Point the suite at your login folders**, not copies
([what each container sees](benchmarks/aacr/README.md#logins)):

```sh
export AACR_CODEX_AUTH_DIR=~/.codex
export AACR_PI_AUTH_DIR=~/.pi/agent
```

**4. Generate one review case:**

```sh
.venv/bin/python -m benchmarks.aacr.convert \
  --upstream ../aacr-bench \
  --output artifacts/aacr/quickstart \
  --daydream-wheel .cache/daydream-wheel/daydream-0.30.0-py3-none-any.whl \
  --task bluewave-labs-checkmate-1897
```

**5. Run it and open the viewer:**

```sh
.venv/bin/harbor run -c experiments/aacr-baseline.yaml -p artifacts/aacr/quickstart \
  -k 1 -n 1 -r 0 --job-name quickstart
.venv/bin/harbor view jobs
```

The [AACR guide](benchmarks/aacr/README.md) covers full runs, reruns and reading
results.
