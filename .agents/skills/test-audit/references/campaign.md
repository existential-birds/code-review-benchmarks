# Test-pruning campaign

Audit one subsystem's entire test surface, such as conversion/task assets,
reviewer integration, or judge/scoring. The value, scope, isolation, and validation
rules in [SKILL.md](../SKILL.md) apply. Use the defect procedure as soon as a
failure appears, including a baseline failure.

## 1. Establish the baseline

Pin the base SHA and record pending work, production/test/support line counts,
collected test identifiers, scenario rows, baseline results, and prerequisites.
Use isolated checkouts for controls without disturbing user edits; also isolate
external resources and outputs. Record failures separately with reproduction
commands. If runtime is a goal, fix selection, workers, retries, coverage, and
setup/teardown settings before measuring.

Done when every in-scope file has a reproducible result or an explicit prerequisite
blocker. A scaffold with no tests is a coverage gap, not a pruning opportunity.

## 2. Inventory by production owner

Assign every test declaration or generated row to one owner boundary. A journey
keeps one owner even when it crosses converter, Harbor, reviewer, and scorer.
Include unit, integration, contract, tooling, and live-provider tests that actually
exist, plus their shared fixtures, packaged assets, runner selectors, and support.
Give shared files one coordinator.

Done when every declaration/row and support file is accounted for, and required
contracts without proof are recorded as gaps.

## 3. Build a read-only ledger

For large surfaces, delegate bounded discovery with explicit ownership when
permitted. Read every assigned test, row, fixture dependency, production owner,
caller, relevant history, and runner selection. Record the
[candidate evidence](../SKILL.md#evidence-before-editing), then classify:

| Mark | Evidence |
| --- | --- |
| Retain | Contract and credible regression detected; record moves here too. |
| Repair | Retained contract with a vacuous or misdirected assertion. |
| Consolidate | Named keeper and unique assertions to transfer first. |
| Delete | Equivalent remaining proof, or evidence no contract needs proof. |

Judge executed paths, not names. For example, zero findings after a failed
reviewer process does not prove a clean review is scored correctly. Establish a
positive control and distinguish failed trials from successful empty findings.

Done when every declaration/row has a supported disposition. Retain uncertain
candidates until evidence resolves them.

## 4. Challenge the layers

In a second read-only pass, name the primary keeper for each contract and cases
to move. Prefer exercising the actual converter, verifier, or adapter over mocks
of its internals; preserve unit tests with independent edge cases. Integration
proof that this repo passes Harbor configuration correctly differs from retesting
Harbor's scheduler. Preserve provider protocol tests where their risks differ.

Done when each owner has a cutover plan, keepers, support changes, validation, and
gap dispositions. A coverage percentage alone cannot justify retiring a layer.

## 5. Cut over

Apply the plans by owner, with disjoint worker ownership and shared support
serialized through its coordinator. Move unique assertions before deletion.
Remove test-only seams only after checking all consumers and public contracts.
Preserve credential/fault cleanup, controlled time, immutable case data, isolated
artifacts, and runner collection. Any new custom code needs the Harbor source,
documentation, and test evidence required by the root instructions.

Done when every selected plan is applied, keepers and support consumers pass,
and all intended retained cases are still selected.

## 6. Review preservation

Obtain an independent read-only comparison of deleted coverage with keepers when
available. Supply the baseline, inventory, ledger, plans, and final diff. Look
for lost sole proof, fakes supplying promised output, unreachable assertions,
hidden provider assumptions, changed failure denominators, and lost artifact
identity or configuration propagation.

For restored or uncertain transferred contracts, deliberately mutate the owner in
an isolated checkout where practical, confirm keeper failure for the intended
behavior, restore exact prior bytes, and rerun. A collection/build failure is not
a caught behavioral mutation. Do not change upstream Harbor or run paid/live
evaluations without appropriate scope and prerequisites.

Done when review gaps have evidence-backed dispositions and no mutation remains.
Report unavailable preservation proof explicitly.

## 7. Resolve defects within scope

Classify retained failures as environment, harness, or product defects. Repair
relevant owners within authorization; separate defect fixes from pruning in the
report and in commits when committing is authorized. Show pre-fix failure and
post-fix success through the same boundary and inputs. Keep unrelated defects
outside campaign edits and report them. A failure never justifies weakening its
assertion, verification settings, or Git hooks.

Done when each required repair has failing-control and passing-candidate proof;
unresolved required defects remain blockers.

## 8. Reconcile and hand off

Refresh against the current base before landing. If upstream changed a retired
test, transfer every new contract or retain the file. Rerun affected consumers
and the [discovered gates](../SKILL.md#validation-and-handoff) after reconciliation.
Authorized commits and pushes must succeed through normal enabled hooks.

Report the skill's handoff fields, baseline/final counts, retired layers, keepers,
preservation gaps and mutation results, defect controls, and comparable runtime
when requested. Distinguish completed scope from remaining work.

Adapted from Daydream Cloud's test-audit campaign guidance, which credits
[OpenClaw's campaign guide](https://github.com/openclaw/openclaw/blob/main/.agents/skills/test-audit/CAMPAIGN.md).
OpenClaw's copyright and MIT terms are retained in the [license](../LICENSE).
