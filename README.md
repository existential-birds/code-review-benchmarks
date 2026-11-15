# Code Review Benchmarks

Measures how well [Daydream](https://github.com/existential-birds/daydream) reviews
code, so we can tell whether a new version or configuration is actually better.
Each pull request in a benchmark becomes a [Harbor](https://github.com/harbor-framework/harbor)
task: Daydream's committed review, its only reviewer since 0.35.0, reviews it in a
sandbox, then a separate judge matches the findings against human-reviewed reference
findings. The benchmarks are
[AACR-Bench](benchmarks/aacr/README.md) and [ReviewBench](benchmarks/reviewbench/README.md).

Scores are precision, recall and their F1, pooled across review cases. They aren't
comparable with either benchmark's published results, because the judge differs;
see [score equivalence](docs/citing.md#score-equivalence).

Every benchmark runs the one Daydream build pinned in
[`benchmarks/daydream/build.json`](benchmarks/daydream/build.json). To measure another
version, change that file, run `make setup` and regenerate the catalogs; Harbor
refuses to run a task generated for a different pin. To score an unreleased commit
without changing the pin, build it with `--candidate <commit>` and pass the
`build.json` it prints to a converter's `--build`.

## Quick start

You need Docker, Git LFS, [`uv`](https://docs.astral.sh/uv/), Node 22.19+ and `make`,
plus two logins: [Codex](https://github.com/openai/codex) signed in with a ChatGPT
subscription (`codex login`) for the reviewer, and [Pi](https://github.com/earendil-works/pi)
logged in to an OpenAI Codex subscription (`/login`) for the judge.

```sh
git clone https://github.com/existential-birds/code-review-benchmarks
cd code-review-benchmarks
make quickstart
```

This installs the pinned Harbor and Daydream into `.venv`, generates one AACR review
case and runs it: Daydream reviews the pull request in a container, the judge scores
its findings, and Harbor prints the scores. `make view` opens the viewer on the
result, and `make help` lists the other commands.

The containers use the logins Codex and Pi already keep, in `~/.codex` and
`~/.pi/agent` unless `CODEX_HOME` or `PI_CODING_AGENT_DIR` says otherwise. To point
elsewhere, set `REVIEWER_CODEX_AUTH_DIR` and `JUDGE_PI_AUTH_DIR` to the real folders,
not copies ([what each container sees](benchmarks/aacr/README.md#logins)). Add
`CASE=<name>` to review a different AACR case.

The [AACR guide](benchmarks/aacr/README.md) covers full runs, reruns and reading
results. The [ReviewBench guide](benchmarks/reviewbench/README.md) runs the same
reviewer and logins on ReviewBench's pull requests.
