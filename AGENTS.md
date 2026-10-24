# Agent guidance

## Never bypass Git hooks

Never use `--no-verify`, hook-disabling environment variables, or any other
mechanism that skips commit, push, or repository verification hooks. This is
never permitted, even when a failure appears unrelated or pre-existing. Fix
the failure on the current branch and retry the ordinary command with hooks
enabled, or stop and report the blocker. A commit or push is complete only
after the normal hook-enabled command succeeds.

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

## Agent skills

### Issue tracker

Track issues and specs in GitHub Issues. Read `docs/agents/issue-tracker.md` before tracker operations.

### Triage labels

Use the five default triage labels. Read `docs/agents/triage-labels.md` before assigning triage roles.

### Domain docs

Use a single-context layout. Read `docs/agents/domain.md` before exploring the codebase.
