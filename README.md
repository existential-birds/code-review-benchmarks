# Code Review Benchmarks

Harbor-based evaluation suite for measuring the effects of changes to
[Daydream](https://github.com/existential-birds/daydream). Other code-review
agents can provide comparison points.

The first integration is a single-case [AACR Oracle verification fixture](benchmarks/aacr/README.md)
using native Harbor execution and a Pi Codex subscription judge. It validates
conversion and scoring; the Daydream baseline remains future work.

## Structure

The suite uses this layout:

```text
benchmarks/
  aacr/           # AACR conversion, task definitions, verifier, and metrics
experiments/      # Native Harbor job configurations
```

Each benchmark retains its own reference data, scoring rules, and reported
metrics. Add shared utilities when integrations actually need the same behavior.
Use Harbor's built-in job configuration, task selection, attempt counts,
concurrency, timeouts, retries, result storage, and aggregation where supported.
Project-specific code covers benchmark conversion and verification plus the
Daydream and subscription-judge adapters required to connect them.

Reuse Daydream's existing ReviewBench integration where practical, with Harbor
owning execution behavior.
ReviewBench benchmark tooling currently lives in Daydream and will eventually
move into this suite.

## Evaluation scope

Evaluate Daydream's complete review pipeline, including exploration,
adjudication, and filtering. Use review-only mode so the output is the findings
a user would receive before any fixes are applied.

The primary goal is deciding whether a Daydream version or configuration
improves review quality at an acceptable cost and latency. This is an internal
measurement tool. Leaderboard submissions, exact upstream score equivalence,
and third-party reproduction are outside the current scope.

## Initial delivery

- Establish one baseline of Daydream's review pipeline; a candidate build is
  not needed yet.
- Use Daydream's Codex backend with its current default deep-review profile
  and model settings, including precision mode off.
- Validate execution and scoring on 10 AACR pull requests before running the
  full available AACR set.
- Reuse the existing AACR scorer with Codex as the sole judge, authenticated
  through the operator's subscription.
- Add other judges only when there is a concrete need; additional scoring modes
  and upstream compatibility checks are outside the initial scope.
- Run evaluations manually on demand, with the operator choosing which cases
  to run or rerun. Configure attempts per case through Harbor's native
  `-k` / `--n-attempts` option or `n_attempts` job setting, using Harbor's default
  when omitted. CI integration is outside the initial scope.

## Comparison discipline

- Keep benchmark cases and scoring settings consistent within a comparison.
- Save raw findings, per-case results, and the settings needed to interpret them.
- Report benchmark scores, completion rate, usage, and latency separately;
  include monetary cost when it is available.
- Keep evaluation inputs and results separate from training data.

AACR reference agreement measures agreement with annotated comments. Any claim
about actual bug precision requires separate validation.

## License

Project code is licensed under [Apache License 2.0](LICENSE). Imported benchmark
materials retain their upstream licenses and attribution.
