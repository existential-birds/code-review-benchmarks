---
name: prepare-implement
description: Use when preparing a GitHub issue or spec for implementation in a fresh session, including a working branch and a copyable handoff prompt.
---

# Prepare Implement

Prepare requested work for a fresh session. End with the prepared branch, the source
reference and one self-contained, copyable implementation prompt. Don't implement;
write a separate handoff file only if asked.

## Scope and branch

- Read `AGENTS.md`, `CONTRIBUTING.md`, `GLOSSARY.md` and `README.md`, then the full
  issue or spec in `existential-birds/code-review-benchmarks` with its comments,
  amendments and linked prerequisites. Note which later decisions replace earlier
  requirements.
- Check the branch, HEAD, worktree and remotes, and keep any existing edits. Create a
  descriptive branch, with the issue number when there is one, from the requested
  base or the current checkout, and record its SHA. Inspect an existing branch before
  reusing it. If existing edits block a safe checkout, leave them and report it.
- Find the build and test configuration, hooks, CI and PR guidance in this checkout.
  Don't borrow commands from other repositories.

## Gather context

Use read-only subagents for separate areas where useful, such as conversion,
scoring, reviewer and judge adapters, and tests. Ask each for concrete paths and
symbols, contracts to keep, test seams, dependencies and risks. Without subagents,
gather the same evidence yourself and say so.

If the work might need custom code, check Harbor's source, docs and tests at its
pinned revision first, and record what Harbor already provides and the exact gap, as
[Harbor first](../../../CONTRIBUTING.md#harbor-first) requires.

Reconcile the findings, mark open questions, and propose disjoint file ownership with
one owner for shared files. Record only checks you actually ran.

## Write the prompt

Include:

- A `/goal ...` line with the desired outcome, the issue and the intended behavior.
  Add commit, push or PR steps only if requested or already authorized.
- The repository path, branch, baseline SHA, existing edits and any blocker, with
  instructions to keep existing changes and leave unrelated ones out of commits.
- The source by reference, material amendments, scope limits and non-obvious
  context, in glossary terms. Name benchmark data, scoring settings and metric
  meanings that must not change.
- The Harbor evidence and the smallest supported place to change. A gap in a Daydream
  wrapper isn't evidence that Harbor lacks the feature.
- Worker subagents with disjoint ownership where the work allows, told they share
  the checkout and mustn't revert each other, plus an independent read-only review.
- The validation commands found in this checkout, and for a bug a failing
  reproduction through an existing public boundary. Keep fixture tests apart from
  live reviewer, judge or full benchmark runs, and keep costly runs within scope.
- Done criteria: acceptance criteria met, checks run, review findings addressed, and
  failures, skips or missing checks reported. The prompt may mention locally
  installed review or PR skills but must work without them.
- For authorized Git work: stage explicit paths and keep hooks enabled. For a PR,
  follow the template, link the issue and check CI on the latest head. The handoff
  never authorizes merging.

End with the prompt and any unresolved blocker, not a generic plan.
