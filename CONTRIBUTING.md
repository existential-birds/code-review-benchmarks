# Contributing as an agent

## Before exploring

Use a single-context layout. Read the
[domain guide](docs/agents/domain.md) before exploring the codebase; it defines
when to consult the glossary and architecture decision records.

## Harbor first: absolute minimum custom code

Use Harbor's built-in behavior, configuration, and supported extension points
wherever possible. Keep custom code to the absolute minimum required.

Before writing any custom code, inspect the Harbor checkout at
`/Users/ka/github/harbor` and prove that the required capability is not already
included. Check the relevant source, documentation, and tests. Record the
inspected paths or symbols, what Harbor already provides, and the precise gap
that requires custom code before implementing it. Assumptions, memory, or the
absence of a feature in Daydream's current wrapper are not sufficient evidence.

Use native Harbor job configuration and controls for task selection, attempt
count (`k`), concurrency, timeouts, retries, scheduling, result storage,
aggregation, and inspection wherever supported. Do not duplicate those
capabilities with a suite runner, custom loops, configuration schemas, or
parallel execution infrastructure. Any necessary custom integration must cover
only the demonstrated gap and use Harbor's supported interfaces.

## Issues and triage

Track issues and specs in GitHub Issues. Read the
[issue-tracker guide](docs/agents/issue-tracker.md) before tracker operations.

Use the five default triage labels. Read the
[triage-label guide](docs/agents/triage-labels.md) before assigning triage roles.

## Verification

Discover the relevant checks from the current project configuration and the
affected benchmark's documentation. Run them against the final changes.

Apply the Git-hook requirements in [AGENTS.md](AGENTS.md) when committing or
pushing.

## Pull requests

Before creating or updating a PR, read and follow the
[PR template](.github/pull_request_template.md). It is the source of truth for
required sections, checklist items, and verification evidence. Use the local
[`pr` skill](.agents/skills/pr/SKILL.md), when available, for Summary examples.

Keep every checklist item. Check an item only after documenting either its
completed work and evidence, or `Not applicable` with a concrete reason. Leave
failed, skipped, or blocked applicable items unchecked and explain the blocker
and remaining uncertainty in the verification evidence.

Before requesting review or handing off the PR, account for every checklist
item and ensure the description reflects the final changes and verified
revision. Report unresolved checks explicitly; an unchecked item must never
silently disappear from the description.
