# Contributing

Run `make test` locally (installs `.venv` first if needed);
[`tests.yml`](.github/workflows/tests.yml) is skipped (`if: false`), so GitHub
checks don't run the suite. Use [glossary](GLOSSARY.md) terms.

## Harbor first

Delegate job execution to Harbor's CLI, config and extension points: task
selection, `-k` attempts, `-n` concurrency, timeouts, `-r` retries, scheduling,
storage, job-level aggregation and inspection. The [loop](loop/) sits on top of
`harbor run` to score and compare candidate builds across rounds.

Before writing custom code, check Harbor at `HARBOR_COMMIT` in the
[`Makefile`](Makefile) (installed in `.venv`) to confirm the capability isn't built
in. Cover only that gap through supported interfaces, and record the inspected
paths and gap in your PR.

## Pull requests

Follow the [PR template](.github/pull_request_template.md): keep every checklist
item, check one only with its evidence or N/A reason, and leave failed, skipped or
blocked items unchecked and explained.
