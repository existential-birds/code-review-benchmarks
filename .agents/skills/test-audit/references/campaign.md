# Test-pruning campaign

Audit one subsystem's whole test surface, such as conversion, reviewer integration,
or judge and scoring. The rules in [SKILL.md](../SKILL.md) apply. Jump to step 7 as
soon as a test fails, including at baseline.

1. **Baseline.** Pin the base SHA and record pending work, line counts, collected test
   IDs, results and prerequisites, using isolated checkouts and resources. Fix runner
   settings before measuring runtime. *Done when* every in-scope file has a
   reproducible result or a named blocker. Missing tests are a gap, not something to
   prune.
2. **Inventory.** Assign every test and generated case to one production owner; an
   end-to-end journey keeps one owner. Include shared fixtures, packaged assets and
   selectors, with one coordinator for shared files. *Done when* everything is
   assigned and contracts without proof are recorded as gaps.
3. **Ledger.** Read every assigned test with its fixtures, owner, callers and history,
   record the [evidence](../SKILL.md#evidence-before-editing), and mark it Retain,
   Repair, Consolidate (naming the keeper) or Delete (naming the equivalent proof).
   Judge executed paths, not names: zero findings after a failed reviewer process
   doesn't prove a clean review scores correctly. *Done when* every test has a
   supported mark; keep uncertain ones.
4. **Challenge the layers.** Name the main keeper for each contract and the cases to
   move. Prefer exercising the real converter, verifier or adapter over mocking its
   internals, keep unit tests with independent edge cases, and don't retest Harbor's
   scheduler. *Done when* each owner has a cutover plan; a coverage percentage alone
   can't retire a layer.
5. **Cut over.** Apply the plans by owner, moving unique assertions before deleting,
   and remove test-only seams only after checking every consumer. Keep credential and
   fault cleanup, controlled time, immutable case data and isolated artifacts.
   *Done when* keepers and support consumers pass and retained cases are still
   collected.
6. **Review preservation.** Get an independent comparison of deleted coverage against
   the keepers. For uncertain transfers, break the owner in an isolated checkout,
   confirm the keeper fails for the right reason, restore it and rerun; a collection
   error doesn't count. *Done when* gaps have dispositions and no deliberate breakage
   remains.
7. **Resolve defects.** Classify failures as environment, harness or product. Fix
   in-scope owners with before-and-after proof through the same boundary, and keep
   defect fixes separate from pruning. Never weaken an assertion, a setting or a hook
   to pass. *Done when* each required fix has proof; unresolved ones are blockers.
8. **Reconcile.** Refresh against the current base, carry over any new contracts in
   retired tests, and rerun the [gates](../SKILL.md#validate-and-hand-off). Report
   baseline and final counts, retired layers, keepers, gaps, breakage results and
   defect proof.

Adapted in part from
[OpenClaw's campaign guide](https://github.com/openclaw/openclaw/blob/main/.agents/skills/test-audit/CAMPAIGN.md).
OpenClaw's copyright and MIT terms are retained in the [license](../LICENSE).
