---
name: prepare-implement
description: Use when preparing a GitHub issue or spec for implementation in a fresh session, including a working branch and a copyable handoff prompt.
---

# Prepare Implement

Prepare the requested work for a fresh session. Finish with the prepared branch,
source reference, and one self-contained, copyable implementation prompt.
Preparation ends with the handoff; leave implementation to the next session.
Create a separate handoff file only when requested.

## Establish scope and branch

- Read `AGENTS.md`, `docs/agents/domain.md`, the root `GLOSSARY.md`, relevant
  `docs/adr/`, and `README.md`. Follow `docs/agents/issue-tracker.md` when reading
  the full issue/spec, comments, amendments, and linked prerequisites from
  `existential-birds/code-review-benchmarks`. Resolve superseding decisions and
  distinguish them from stale linked requirements.
- Inspect the current branch, HEAD, worktree status, and remotes. Preserve and
  record existing edits. Create and check out a descriptive branch, using the
  issue number when available. Use the user's requested base, otherwise the
  current checkout, and record its exact SHA. Fetch when establishing a remote
  base. Inspect an existing branch before reusing it for resumed work; otherwise
  choose a unique name. If preserving edits prevents a safe checkout, leave them
  in place and report the branch blocker.
- Confirm the actual branch before gathering context. Inspect the current
  repository for build/test configuration, hooks, CI, and PR guidance. This repo
  began as a scaffold: planned benchmark directories and README examples are not
  proof that implementations or verification commands exist. Record unavailable
  checks rather than importing Daydream Cloud's commands.

## Gather implementation context

Use subagents for distinct, bounded, read-only investigations where useful, such
as benchmark conversion, verifier/scoring behavior, reviewer or judge adapters,
and tests/fixtures. Give each agent the source reference and repository guidance.
Request concrete paths and symbols, contracts to preserve, existing public test
seams, dependencies, and unresolved risks. Agents leave file edits and
implementation to the new session. If delegation is unavailable, gather the same
evidence directly and state that limitation.

For work that could require custom code, inspect the Harbor checkout at
`/Users/ka/github/harbor` before proposing an implementation. Check relevant
source, documentation, and tests. Record inspected paths or symbols, what Harbor
already provides, and the precise remaining gap. Harbor owns job configuration,
task selection, attempt count (`k`), concurrency, timeouts, retries, scheduling,
result storage, aggregation, and inspection wherever supported. Prefer native
configuration and supported extension points; narrow any custom integration to
the demonstrated gap. Explain any conflict with
`docs/adr/0001-use-native-harbor-execution.md` rather than silently replacing its
decision.

Collect every agent's findings and reconcile shared contracts before writing the
prompt. Resolve conflicts with focused reads, and mark unanswered questions
explicitly. Propose disjoint implementation ownership and one owner for shared
schemas, fixtures, or integration files. Record only checks actually run;
context gathering alone does not establish passing tests.

## Write the new-session prompt

Include:

- The user's desired outcome as an explicit `/goal ...` line, with the actual issue and
  intended behavior. Preserve the requested scope. Include commit, push, or PR
  publication steps only when requested or already authorized; preparation
  itself does not perform them or launch another session.
- Repository path, prepared branch, exact baseline SHA, existing edits, and any
  branch blocker. Tell the next session to confirm state, preserve existing
  changes, and exclude unrelated changes from its commits.
- The complete authoritative source by reference, material amendments, scope
  boundaries, and the non-obvious context gathered. Use glossary terms such as
  review case, reviewer, judge, trial, and attempt count. Preserve benchmark
  reference data, scoring settings, and reported metric meanings when relevant.
- Harbor evidence and the smallest supported implementation seam. Carry forward
  unresolved gaps for inspection before custom code is written. A missing
  feature in a Daydream wrapper is not evidence that Harbor lacks it.
- Instructions to use worker subagents with explicit, disjoint file/module
  ownership where the work supports it, and an independent read-only review.
  Tell workers they share the codebase and must accommodate others' changes
  without reverting them. Assign shared contracts and integration to one owner.
- Concrete validation seams and commands discovered in this checkout. Choose
  checks appropriate to the change; for behavioral bugs identify a failing
  reproduction through the existing public boundary. Separate deterministic
  fixture proof from live reviewer/judge or full benchmark evidence. Keep costly
  evaluation runs within the requested scope and use native Harbor controls.
- Review and completion criteria: requested acceptance criteria met, applicable
  checks run, actionable review findings addressed, and actual failures, skips,
  or missing checks reported. Reference local `code-review` and `pr` skills when
  applicable and available; keep the prompt executable without them. If using
  `implement`, state these repository-specific criteria alongside it.
- For authorized Git operations, stage related paths explicitly and keep normal
  hooks enabled. Fix hook failures on the current branch and retry, or report
  the blocker. For an authorized PR, use the current repository PR guidance,
  link the issue, and inspect required CI on the latest PR HEAD. Never claim
  missing or disabled checks passed. The handoff does not authorize merging.

End with the prompt and any unresolved preparation blocker. Do not substitute a
generic plan for the concrete branch state, Harbor evidence, and validation
seams needed by the next session.
