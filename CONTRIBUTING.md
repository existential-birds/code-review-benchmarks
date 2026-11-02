# Contributing

Open an issue before starting a change, so we can agree on the approach first.

## Setup

Follow the [quick start](README.md#quick-start) to install the suite, then add the
test dependency and run the tests:

```sh
uv pip install --python .venv/bin/python pytest==8.4.2
.venv/bin/python -m pytest tests -q
node --test tests/pi_judge.test.mjs
```

Use the terms in the [glossary](GLOSSARY.md) and check the
[decision records](docs/adr/) before changing how the suite works.

## Harbor first

Use Harbor's built-in behavior, configuration and extension points wherever
possible, and keep custom code to the minimum required. That covers task selection,
attempt count (`k`), concurrency, timeouts, retries, scheduling, result storage,
aggregation and inspection. Don't add a suite runner, custom loops, configuration
schemas or parallel execution infrastructure.

Before writing custom code, check Harbor's source, documentation and tests at the
pinned revision to confirm the capability isn't already there. Record what you
inspected and the precise gap in your pull request. Any custom integration should
cover only that gap and use Harbor's supported interfaces.

## Pull requests

Follow the [PR template](.github/pull_request_template.md). Keep every checklist
item, and check one only after recording its evidence or why it doesn't apply. Leave
failed, skipped or blocked items unchecked and explain them.

Keep Git hooks enabled; see [AGENTS.md](AGENTS.md).
