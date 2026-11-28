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

## Versioning and changelog

This repository follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html),
with `version` in [`pyproject.toml`](pyproject.toml) as the source of truth. While on
`0.y.z` initial development, apply SemVer's `0.y.z` rules while still calling out breaking
changes and migration steps explicitly. From `1.0.0` onward, incompatible public changes
bump major, compatible features or deprecations bump minor, and compatible fixes bump patch.

[`CHANGELOG.md`](CHANGELOG.md) follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/). Add notable changes under
`[Unreleased]` using `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, or `Security`,
and link the issue or PR when available. Never put held-out split scores or findings in
changelog entries or release notes. If a PR has no notable change, mark the changelog
checklist item N/A with a brief reason.

## Releases

Cut a release in two steps:

1. Open a release PR targeting `main` that sets `version` in `pyproject.toml` (when
   bumping), moves `[Unreleased]` entries in `CHANGELOG.md` into `## [X.Y.Z] - YYYY-MM-DD`
   (newest release first), updates the comparison links at the bottom of `CHANGELOG.md`,
   and passes `make test`.
2. After the release PR merges to `main`, pull `main` cleanly, create and push a signed
   annotated tag on the merge commit, and publish the GitHub release with that changelog
   section:

   ```sh
   git tag -s -a vX.Y.Z -m "Release vX.Y.Z"
   git push origin vX.Y.Z
   gh release create vX.Y.Z --title "vX.Y.Z" --notes-file <notes-file>
   ```
