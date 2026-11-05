---
name: refactor-audit
description: Use for pre-merge refactor sweeps or codebase audits targeting duplicated responsibilities, unnecessary layers, overengineering, or LLM-generated complexity in code-review-benchmarks.
---

# Refactor Audit

Find a simpler design for the behavior that must stay: fewer owners of each
responsibility, fewer state models, translations and layers, and simpler callers.
That can take a large diff.

## Scope

A sweep request means implementing supported refactors in scope; review-only and
planning requests stay read-only. Commits, pushes, merges, feature removal and
external changes need existing authorization.

Read `AGENTS.md`, [GLOSSARY.md](../../../GLOSSARY.md) and the relevant requirements,
pin a baseline, keep pending work, and find the verification commands in the
configuration. For a PR, follow the diff into whole workflows and their callers,
including unchanged code. For a codebase sweep, list the subsystems. Respect the
user's effort limit and report what you actually covered.

## Harbor first

Read [Harbor first](../../../CONTRIBUTING.md#harbor-first). Look first for custom code
that duplicates what Harbor owns: job configuration, task selection, attempts,
concurrency, timeouts, retries, scheduling, result storage, aggregation and
inspection. Before replacing a wrapper, check the pinned Harbor source, docs and
tests, record what Harbor provides and the exact gap, and trace the wrapper's callers
so benchmark scoring, artifacts and adapter behavior survive. Editing Harbor or
Daydream needs its own scope.

## Architecture before cleanup

Trace each workflow from its entry point to the owner of its policy and state,
through I/O, to its result. Read implementation, callers, tests, exports,
configuration and runtime targets, not just large files or repeated text. For broad
work, delegate independent workflow searches and a separate interface challenge when
allowed; otherwise do the challenge pass yourself and say so.

Ask at each boundary:

- What complete operation should the caller request, and which forwarding layers,
  factories, copies or registries would that remove?
- Who owns each decision, snapshot and resource lifetime? Where do callers repeat
  setup, validation, cleanup or reads because an interface is partial?
- Which separately updated flags describe one resource or trial? Could one owner
  allow only legal states and publish results atomically?
- Which abstractions serve real variation? Could Harbor, a plain function or an
  existing interface replace speculative options?

For strong leads, sketch the current interface, the proposed one, the caller
migrations and what gets removed. Read
[the structural challenge](references/structural-challenge.md) before settling for
small cleanups or calling a change too risky. No change is a valid outcome after that
investigation; don't force an abstraction or a line-count reduction.

## Implement and verify

Rank by maintenance benefit and scope, then by evidence and verification cost; a
small diff isn't the goal. A risk objection must name an invariant and a failure
mode. Finish each change, including caller migration and removing superseded paths,
then re-read the whole workflow and reassess the remaining leads.

Preserve review-case and reference-finding fidelity, captured inputs, review-only
behavior, reviewer and judge separation, scoring rules and failure denominators, raw
evidence, credential isolation and attribution. Also preserve timing contracts:
snapshots, cancellation, ordering, timeouts, retries, partial output and error
propagation. Trace success and failure paths before collapsing orchestration. Change
surprising behavior only as an explicit, tested change, and add characterization
tests before refactoring. Use [test-audit](../test-audit/SKILL.md) for test changes.

Run focused behavior checks and the required gates on the final source, exercising
converter, verifier and adapter output; compiling isn't enough. Live reviewer or judge
runs need authorized scope. Get an independent preservation review when available,
run `git diff --check`, and keep hooks enabled.

## Report

- Structural changes: old and new interfaces, what was removed, caller impact, and
  Harbor evidence for custom-code decisions.
- Local cleanups separately, plus larger leads tried, ruled out or deferred, with
  evidence.
- Before and after line counts for production code, tests and docs, counting
  replacements and not padding reductions with reflowed lines.
- Commands and results, the preservation review, unverified paths, remaining scope
  and blockers.
