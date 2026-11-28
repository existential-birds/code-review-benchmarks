# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-11-28

### Added

- Harbor-native benchmark converters, Docker sandboxes, pooled scoring, and dev/held-out splits for
  [AACR-Bench](benchmarks/aacr/README.md) (`benchmarks.aacr.convert`, `benchmarks.aacr.score`,
  `benchmarks/aacr/holdout.txt`) and [ReviewBench](benchmarks/reviewbench/README.md)
  (`benchmarks.reviewbench.convert`, `benchmarks.reviewbench.report`, pinned to the PR bytes
  Daydream reviews), with `make quickstart`, `make view`, `make setup`, and `make test`
  ([#6](https://github.com/existential-birds/code-review-benchmarks/pull/6),
  [#22](https://github.com/existential-birds/code-review-benchmarks/pull/22),
  [#28](https://github.com/existential-birds/code-review-benchmarks/pull/28),
  [#40](https://github.com/existential-birds/code-review-benchmarks/pull/40),
  [#68](https://github.com/existential-birds/code-review-benchmarks/pull/68),
  [#72](https://github.com/existential-birds/code-review-benchmarks/pull/72)).
- Single pinned Daydream build manifest (`benchmarks/daydream/build.json`, currently Daydream
  `0.38.1` at `ca1b35e` with the OpenRouter socket-retry fix), `--candidate <commit>` builds for
  scoring unreleased commits without changing the pin, native Daydream capture with Harbor ATIF
  trajectory export, and ChatGPT-subscription judging via Pi `1.6.0` (`benchmarks/pi/judge.mjs`)
  with least-privilege container login isolation
  ([#17](https://github.com/existential-birds/code-review-benchmarks/pull/17),
  [#27](https://github.com/existential-birds/code-review-benchmarks/pull/27),
  [#60](https://github.com/existential-birds/code-review-benchmarks/pull/60),
  [#67](https://github.com/existential-birds/code-review-benchmarks/pull/67),
  [#89](https://github.com/existential-birds/code-review-benchmarks/pull/89)).
- Swappable `[reviewer]`, `[judge]`, and `[proposer]` slots in `loop/settings.toml` (defaulting to
  Daydream on Pi/OpenRouter `deepseek/deepseek-v4.1-flash-1017` as reviewer, Pi `openai/gpt-6.1-sol`
  as judge, and Codex `gpt-6.2-sol` as proposer, with `agy`, `claude`, and `pi` proposer adapters),
  baked into generated catalogs and enforced across score comparisons
  ([#83](https://github.com/existential-birds/code-review-benchmarks/pull/83)).
- Autonomous hill-climbing loop (`loop/`, [`docs/loop.md`](docs/loop.md)):
  - Settings and dev climb/check sets (`loop.settings`, `loop.sets`), multi-round Harbor runner
    (`loop.rounds`), and statistically derived run-to-run noise measurement (`loop.noise`,
    [`docs/loop-noise.md`](docs/loop-noise.md))
    ([#63](https://github.com/existential-birds/code-review-benchmarks/pull/63),
    [#66](https://github.com/existential-birds/code-review-benchmarks/pull/66),
    [#70](https://github.com/existential-birds/code-review-benchmarks/pull/70),
    [#89](https://github.com/existential-birds/code-review-benchmarks/pull/89)).
  - Oracle canary judge-drift detection (`loop.canary`), pooled accept/reject/futility decisions
    (`loop.decide`), hash-chained append-only run ledger (`loop.ledger`), and diff/path/test-integrity
    guard with isolated Daydream `make check` gating (`loop.guard`)
    ([#64](https://github.com/existential-birds/code-review-benchmarks/pull/64),
    [#65](https://github.com/existential-birds/code-review-benchmarks/pull/65),
    [#71](https://github.com/existential-birds/code-review-benchmarks/pull/71),
    [#73](https://github.com/existential-birds/code-review-benchmarks/pull/73)).
  - Climb-set evidence packs (`loop.evidence`), containerized proposer (`loop.propose`),
    crash-resumable single-iteration driver (`loop.iterate`), and isolated held-out confirmation
    (`loop.confirm`) returning a verdict-only outcome (`confirmed`, `unconfirmed`, or `regressed`)
    with a held-out use cap
    ([#74](https://github.com/existential-birds/code-review-benchmarks/pull/74),
    [#75](https://github.com/existential-birds/code-review-benchmarks/pull/75),
    [#76](https://github.com/existential-birds/code-review-benchmarks/pull/76),
    [#77](https://github.com/existential-birds/code-review-benchmarks/pull/77)).
  - Run orchestrator (`loop.run`) with budget, plateau, drift, quota-backoff, resume, and machine
    lock controls; finished-iteration artifact/build/image/job pruning (`loop.prune`); and winner
    landing (`loop.land`) for signed draft Daydream PRs, merge recording (`--merged`), pin-bump
    branch preparation (`--bump`), and baseline pin-gap reporting
    ([#78](https://github.com/existential-birds/code-review-benchmarks/pull/78),
    [#79](https://github.com/existential-birds/code-review-benchmarks/pull/79),
    [#80](https://github.com/existential-birds/code-review-benchmarks/pull/80)).
- Benchmark provenance, citations, score-equivalence guide (`docs/citing.md`), and domain glossary
  (`GLOSSARY.md`) ([#13](https://github.com/existential-birds/code-review-benchmarks/pull/13)).

[Unreleased]: https://github.com/existential-birds/code-review-benchmarks/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/existential-birds/code-review-benchmarks/releases/tag/v0.1.0
