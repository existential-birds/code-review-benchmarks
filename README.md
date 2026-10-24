# Code Review Benchmarks

Harbor-based evaluation suite for [Daydream](https://github.com/existential-birds/daydream)
and other code-review agents.

The first integration will port [AACR-Bench](https://github.com/alibaba/aacr-bench)
to Harbor. This repository currently contains the project scaffold; the AACR
converter and runnable tasks are not implemented yet.

## Structure

As integrations are added, the suite will use this layout:

```text
benchmarks/
  aacr/           # AACR conversion, task definitions, verifier, and metrics
shared/           # Snapshot preparation, manifests, artifacts, and reporting
experiments/      # Pinned agent configurations and reproducible comparisons
```

Each benchmark retains its own reference data, scoring rules, and reported
metrics. Shared tooling handles execution and evidence collection. Daydream's
execution adapter stays in the Daydream repository; experiments select a pinned
Daydream build.

## Reproducibility

- Pin upstream dataset and scorer revisions, with checksums and provenance.
- Freeze repository snapshots, agent builds, model settings, judge settings, and
  execution budgets for each experiment.
- Preserve raw findings and per-task results so runs can be rescored.
- Validate imported scoring against upstream before reporting results.
- Report benchmark scores, completion rate, cost, and latency separately.
- Keep evaluation inputs and results separate from training data.

AACR reference agreement measures agreement with annotated comments. Any claim
about actual bug precision requires separate validation.

## License

Project code is licensed under [Apache License 2.0](LICENSE). Imported benchmark
materials retain their upstream licenses and attribution.
