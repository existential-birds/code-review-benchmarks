---
name: refactor-audit
description: Use for pre-merge refactor sweeps or codebase audits targeting duplicated responsibilities, unnecessary layers, overengineering, or LLM-generated complexity in code-review-benchmarks.
---

# Refactor Audit

Find a simpler design for the behavior that must remain. Prioritize fewer
responsibility owners, state models, translations, and indirections with simpler
callers. Removing an unnecessary architecture can require a substantial diff.

## Scope and baseline

A sweep request defaults to implementing supported refactors within its scope.
Explicit review-only or planning requests stay read-only. Commit, push, merge,
feature removal, and external changes require existing user authorization.

Read root `AGENTS.md`, [GLOSSARY.md](../../../GLOSSARY.md), and the selected
requirements and public contracts.
Pin a baseline, preserve pending work, and discover verification commands from
actual configuration.

In a PR, use the diff to enter whole owning workflows and their callers, including
unchanged code. For a codebase sweep, inventory implemented subsystems. Honor the
user's effort limit and report actual coverage; do not create speculative modules.

## Harbor before custom architecture

Read [Harbor first](../../../CONTRIBUTING.md#harbor-first). Harbor owns
job configuration, task selection, attempt count, concurrency, timeouts, retries,
scheduling, result storage, aggregation, and inspection where supported. Look
first for custom code that duplicates those responsibilities.

Before writing custom code or replacing a wrapper, inspect relevant source,
documentation, and tests in the pinned Harbor checkout. Record exact paths or
symbols, what Harbor already provides, and the precise gap requiring integration
code. Prefer native configuration and supported extension points. Trace the
wrapper's callers and outputs before removing it so benchmark-specific scoring,
artifact contracts, and adapter behavior survive. Editing Harbor or Daydream
requires its own authorized scope.

## Architecture before cleanup

Trace entry → policy/state owner → I/O → result for each in-scope workflow.
Inspect implementation, callers, tests, exports, configuration, and runtime
targets. Look beyond large files, repeated text, and static rule hits.

For broad work, delegate independent workflow searches and a cross-cutting
interface challenge when permitted. Give implementers explicit ownership, tell
them they share the codebase, and preserve others' edits. Coordinate shared
changes and serialize runs sharing mutable resources. Without delegation,
perform a separate challenge pass and state that limitation.

Ask at the design boundary:

- What complete operation should a caller request? Which forwarding layers,
  factories, DTO copies, or registries would that eliminate?
- Who owns each decision, snapshot, and resource lifetime? Where do callers repeat
  acquisition, validation, cleanup, or reads because an interface is partial?
- Which independently updated flags describe one resource or trial? Can one owner
  represent legal states and publish results atomically?
- Which abstractions support demonstrated variation? Could native Harbor behavior,
  a direct function, or an existing interface replace speculative options?

For strong leads, sketch **current interface → proposed interface → caller
migrations → responsibilities/code removed**. Read
[the structural challenge](references/structural-challenge.md) before settling
for small cleanup or a blanket “too risky/already compact” conclusion.

Expand once to whole workflow/module boundaries and record the strongest larger
lead's disposition. Zero changes or small gains are valid after that investigation.
Do not force an abstraction, feature loss, or numeric reduction to manufacture
success; a cleanup inventory alone does not complete an implementation sweep.

## Select, implement, and verify

Rank maintenance benefit and architectural scope, then evidence and verification
cost. A small diff is not a selection gate. Record paths/callers, before/after
ownership, preserved contracts, replacement cost, and validation. A risk objection
must name an invariant and failure mode.

Complete coherent transformations, including caller migration and removal of
superseded paths. Count the replacement as well as deletions. Reinspect the whole
workflow after integration, then reassess remaining in-scope leads. Stop at an
evidence-backed disposition, concrete blocker, or the user's scope/effort limit.

Preserve review-case and reference-finding fidelity, captured inputs, review-only
behavior, reviewer/judge separation, scoring rules and failure denominators,
raw evidence and configuration, credential isolation, and benchmark attribution.
Preserve meaningful temporal contracts: snapshot timing, cancellation, ordering,
timeouts, retries, partial output, and error propagation. Similar code may protect
different risks; absence of local references does not establish that a public
configuration or artifact is unused.

Trace success and failure completion before collapsing orchestration, including
thrown/returned errors, sentinel values, and catch boundaries. Characterize
surprising behavior; fix it only as an explicit, tested behavior change. Use
`test-audit` for test authorship/consolidation while retaining this skill's design
focus. Preserve behavioral coverage rather than test count. Add missing
characterization before refactoring; reproduce bugs before fixing them.

Run focused behavior checks and discovered required gates on final source, with
the tested checkout immutable during runs. Exercise affected converter, verifier,
and adapter outputs; compilation alone is insufficient. Prefer small fixtures
for deterministic contracts. Live reviewer/judge runs need authorized scope and
prerequisites; an audit does not require an unrequested full benchmark evaluation.
If no harness exists, report the unverified boundary rather than invent commands.
Obtain an independent interface/preservation review when available. Run
`git diff --check`. Normal Git hooks remain enabled; fix failures or report the
blocker before claiming an authorized commit or push succeeded.

## Report the result

- Structural changes: old/new interface, ownership/state/layers removed, caller impact,
  and Harbor capability evidence for custom-code decisions.
- Local cleanup separately; larger leads attempted, ruled out, or deferred with evidence.
- Before/after production, tests/support, and docs/tooling changes, including new
  files/replacements. Distinguish source LOC from diff additions; do not manufacture
  reductions with reflow, line packing, or moving code outside the measured scope.
- Commands/results, preservation review, unverified paths, remaining scope, and blockers.

An unexecuted plan or unrelated green suite is not an implemented, verified sweep.
Publish results only within the user's authorization.
