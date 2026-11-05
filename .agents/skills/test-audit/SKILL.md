---
name: test-audit
description: Use when writing or changing benchmark tests, reviewing their value, or auditing redundant coverage and test support in code-review-benchmarks.
---

# Test Audit

Protect observable contracts with the least test and support code that still catches
credible regressions. **Authoring** checks the tests you're changing, an **audit**
examines one boundary, and a **campaign** covers a subsystem's whole test surface; for
campaigns, read [references/campaign.md](references/campaign.md).

## Scope

An audit request means implementing supported improvements in scope: fix misleading
assertions, fill relevant gaps and remove proven redundancy. Review-only and planning
requests stay read-only, and loading this skill to write a test doesn't authorize a
wider audit. Commits, pushes and upstream changes need existing authorization.

Read `AGENTS.md`, [GLOSSARY.md](../../../GLOSSARY.md) and the work's requirements, and
find the tests, commands and CI in the checkout. Test this repository's integration
with Harbor, not Harbor itself; custom code needs the evidence in
[Harbor first](../../../CONTRIBUTING.md#harbor-first). Don't edit Harbor, Daydream or an
upstream benchmark without that scope.

## Before adding or changing a test

Name:

1. The observable behavior or invariant, and a credible regression it would catch.
2. Why existing tests miss it, and the strongest existing test for that owner. A
   second layer needs its own risk, such as artifact decoding, process failure, or a
   Harbor setting reaching the task.
3. The real boundary exercised, and evidence that the owner is reached. Fake external
   processes, providers, clocks and faults where useful, but don't add production
   exports, flags or delays just for tests.

For a bug, show the same test failing before the fix for the right reason and passing
after. A fake that supplies the corrected result can't prove its production owner.
Prefer adding a case to a useful fixture over new setup.

## Value

Preserve behavioral coverage, not test counts or file names, and move unique
assertions before deleting their home. Look for:

- Self-comparisons, tests without assertions, and expected values computed by the
  code under test.
- Source greps or copied inventories that only freeze internal layout.
- Private-helper or mock-call checks already covered by an observable outcome.
- Fakes or prewritten artifacts that perform the behavior being claimed.
- Duplicate cases across layers without a distinct risk.
- Configuration checks that restate a flag without exercising it.
- Negative cases rejected by an earlier guard, and names stronger than their
  assertions.
- Test-only exports, wrappers, global resets and unused fixtures.

Keep distinct proof of the contracts in scope:

- **Conversion and task assets:** review-case identity, the captured change and
  context, reference-finding fidelity, deterministic output, valid task assets,
  attribution and licenses.
- **Reviewer integration:** the full review-only pipeline, configuration and inputs,
  findings extraction, process failure, partial output and credential isolation.
- **Judge and scoring:** reference matching, malformed output, empty versus failed
  trials, each benchmark's scoring rules, and reference agreement versus real bug
  precision.
- **Harbor integration:** task selection and attempts, adapter interfaces, timeout and
  failure propagation, raw evidence and settings, aggregation denominators, and
  separate score, completion, usage, cost and latency.

Keep byte or schema checks where emitted artifacts are the contract, and protocol
cases where provider differences matter. A controlled-provider test and a real
reviewer or judge run prove different things. Slowness alone doesn't justify deletion.

## Evidence before editing

Read the whole candidate with its fixtures, the production owner, callers,
overlapping tests and history. Before deleting or merging a test, record its ID, the
regression it catches, its owner and evidence the owner is reached, the remaining
tests that catch the same failures, the unique assertions to move, and how you'll
validate. For broad audits, delegate independent batches with explicit ownership,
with one coordinator for shared support. Isolate worktrees and shared resources, and
don't run in parallel anything that resets the same ones.

## Validate and hand off

Record baseline results first. Use the project's own commands and keep thresholds,
lockfiles and hooks. Run the owner tests, consumers of shared support and the
required gates on the final source, and confirm moved cases are still collected. Live
reviewer or judge runs need authorized scope; an audit isn't a full benchmark run.
For large deletions, get an independent preservation review when available. Never
delete a failing test just to go green. Run `git diff --check`, and report
`git diff --numstat` for production code, tests and docs separately.

Report the changed categories, keepers and contracts, rejected deletions, commands and
results, skipped proof and blockers, and whether the change landed.
