---
name: test-audit
description: Use when writing or changing benchmark tests, reviewing their value, or auditing redundant coverage and test support in code-review-benchmarks.
---

# Test Audit

Protect observable contracts with the least test and support code needed for
credible regression detection. **Authoring** gates the tests being changed;
**audit** examines a focused boundary; **campaign** examines a subsystem's entire
test surface. Read [references/campaign.md](references/campaign.md) for campaigns.

## Scope and repository boundary

An audit request defaults to implementing supported improvements within its
scope: repair misleading assertions, fill relevant gaps, and consolidate or
delete proven redundancy. Explicit review-only and planning requests remain
read-only. Loading this skill while authoring a test does not authorize a broader
audit. Commit, push, publication, and changes to upstream repositories require
existing user authorization.

Read root `AGENTS.md`, [domain guidance](../../../docs/agents/domain.md),
[GLOSSARY.md](../../../GLOSSARY.md), relevant [ADRs](../../../docs/adr/), and
the requirements for the selected work. Before GitHub issue operations, read
[the issue-tracker guide](../../../docs/agents/issue-tracker.md). Discover tests,
configuration, commands, and CI from the actual checkout. The initial repository
is a scaffold: planned benchmark paths are not evidence that a test suite exists.

Harbor owns execution controls. Before adding custom code, inspect the relevant
source, documentation, and tests in `/Users/ka/github/harbor`; record the paths or
symbols, built-in capability, and precise remaining gap as required by `AGENTS.md`.
Prefer native Harbor configuration and supported extension points. Audit this
repo's integration contract without duplicating Harbor's own test suite or
editing Harbor, Daydream, or an upstream benchmark outside authorized scope.

## Authoring gate

Before adding or changing a test, identify:

1. Its observable behavior or independent invariant and a credible regression.
2. Why existing coverage misses that regression; name the strongest owner test.
   A second layer needs a distinct risk, such as artifact decoding, process
   failure, or propagation of a native Harbor setting.
3. The real boundary exercised and evidence that the asserted owner is reached.
   Keep external process/provider, clock, and fault controls where useful; avoid
   production exports, flags, wrappers, and delays added solely for tests.

For bug regressions, use the same harness and inputs to show pre-fix failure for
the intended reason and post-fix success. A fake that supplies the corrected
converter, reviewer, judge, or aggregate result cannot prove its production owner.
Prefer adding a scenario to an existing useful fixture over duplicating setup.

## Value and preservation

Preserve behavioral coverage rather than test count, filenames, or scenario rows.
Transfer unique assertions before deleting their home. A keeper must detect the
same relevant failures on equivalent inputs; unchanged collection counts or
coverage percentages do not establish that. When minimizing test code, examine
redundant layers and cases as well as shared setup.

Investigate these low-value patterns:

- Self-comparisons, assertion-free probes, and expected values produced by the
  same implementation under test.
- Source/import greps or copied inventories that merely freeze internal layout.
- Private-helper and mock-call checks duplicated by an observable outcome.
- Fakes or prewritten artifacts that perform the behavior being claimed.
- Duplicate cases across layers without distinct decoding, process, or failure risk.
- Configuration checks that restate a flag without exercising its integration.
- Negative controls rejected by an earlier guard, and names stronger than assertions.
- Dead test-only exports, wrappers, global resets, and unused fixture machinery.

Preserve distinct proof of the implemented contracts that apply to the scope:

- **Conversion and task assets:** review-case identity, captured change/context,
  reference-finding fidelity, deterministic conversion, valid task assets, and
  upstream attribution or license requirements.
- **Reviewer integration:** the complete review pipeline in review-only mode,
  correct configuration and captured inputs, findings extraction, process failure,
  partial output, and credential isolation.
- **Judge and scoring:** reference matching, parsing and malformed output,
  empty/failed trials, benchmark-specific scoring rules, and the distinction
  between reference agreement and independently validated bug precision.
- **Harbor integration and results:** native task selection and attempt count,
  supported adapter interfaces, timeout/failure propagation, raw evidence and
  settings needed to interpret results, aggregation denominators, and separate
  score, completion, usage, cost when available, and latency reporting.

These are retention questions, not a mandate to implement planned features.
Keep byte/schema checks when emitted artifacts are the public contract and
protocol cases when provider differences matter. Controlled-provider tests prove
their modeled behavior; an actual reviewer/judge run proves a different boundary.
Neither substitutes for the other. Slowness alone does not justify deletion.

## Evidence before editing

Read the full candidate and all scenario rows, fixture chain, production owner,
callers, overlapping tests, runner selection, and relevant history. Inspect pinned
dependency source or provider contracts when the conclusion depends on them.
Record, before deletion or consolidation:

- Exact test identifier and affected rows; actual regression detected.
- Production owner, external consumers, and evidence the intended path is reached.
- Named remaining keepers with equivalent inputs/failure modes, or evidence that
  the asserted property is no longer a contract.
- Why the test or seam exists, unique assertions to transfer, support deletion
  unlocked, risk, and focused validation.

Choose a coherent owner batch. For broad audits, delegate independent batches
with explicit file ownership when permitted; one coordinator owns shared support
and integration. Tell workers they share the checkout and must preserve others'
edits. Keep a tested checkout immutable during runs. Isolate worktrees and any
shared containers, providers, ports, caches, and result directories; serialize
runs that can reset the same resources.

## Validation and handoff

Establish baseline results and prerequisites before edits. Discover verification
from current project configuration and documented commands; preserve configured
thresholds, exclusions, lockfiles, and hooks. If no harness exists, report that
gap and the limits of inspection instead of inventing commands or claiming tests
passed.

Run the selected owner tests, sibling/shared-support consumers, and the required
affected gates on the final source. Verify moved cases remain collected. Exercise
actual converter/verifier/adapter output when replacing static checks; use small
deterministic fixtures where they prove the contract. Live reviewer/judge runs
require the user's authorized scope and prerequisites. Do not turn an audit into
an unrequested full benchmark evaluation. Keep review-case selection and Harbor
settings identical for runtime comparisons, including setup/teardown cost.

For substantial deletion, obtain an independent read-only preservation review
against the pinned baseline when available. Investigate baseline failures at
their owner; never remove a failing test merely to turn green. Run `git diff
--check` and inspect `git diff --numstat`, reporting production, tests/support,
and docs/tooling separately. Normal Git hooks must remain enabled for authorized
landing; repair failures and retry normally or report the blocker.

Report changed categories, named keepers/contracts, rejected deletion candidates,
commands and results actually run, skipped proof and blockers, measured runtime
only when comparable, and landing state. Unavailable required proof or unfinished
scope remains explicit; an inventory alone does not complete an implementation
audit.
