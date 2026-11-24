# Seeds

Hypotheses to try when the evidence pack supports them. A seed is a place to start
looking, not a change to make: propose it only if the pack shows the pattern, and
cite what shows it. File references are to Daydream 0.37.0; check them against the
worktree's revision.

## Cross-file Go context lost when a review is split

From existential-birds/daydream#1495. Committed review packs a change's diff into
assignment batches of at most four files, in diff order (`_batches` in
`daydream/review_stage_inputs.py`). A review with more batches than
`REVIEWER_BATCHES_PER_ALLOWANCE` (`daydream/config.py`) is decomposed into separate
assignments followed by a cross-assignment phase (`daydream/committed_review.py`).
Go files in one package share identifiers without importing each other, and a Go
import names a package directory, not a file: `_resolve_import` in
`daydream/deep/dependency.py` maps it to `<path>.go`, so the changed-file import
graph has no Go edges, and today it feeds only diagram eligibility
(`daydream/deep/orchestrator.py`).

Look for Go changes whose missed reference findings need a caller, callee or type
from another changed file that landed in a different batch, and for findings ruled
wrong that a definition in another file would have refuted. Likely category:
`handoff_loss`.

## Model and reasoning effort of the review and arbiter phases on Codex

existential-birds/daydream#1855 settled which model and reasoning effort the
`review` and `arbiter` phases use for Claude, on six local diffs. Ask the same on
Codex, the backend this suite runs. In committed review, `review` is the discovery
phase and `arbiter` the challenge phase (`REVIEW_PHASES` in
`daydream/committed_review.py`). The Codex defaults are in `daydream/config.py`:
`PHASE_DEFAULT_MODELS` (both tiers use `DEFAULT_CODEX_MODEL`) and
`DEEP_PHASE_DEFAULT_EFFORT["codex"]` (`review` medium, `arbiter` high). They are
resolved in `daydream/run_config.py` and `_resolve_backend` in `daydream/runner.py`.

Look at each round's `review_summary.phases` usage, budget stops and incomplete
reviews, beside what each case missed and sent wrong. Change one value of one phase.
The skill treats an effort or model change as a deliberate difference rather than
a matched experiment, so say so in `change.description`, and watch cost: a candidate
much dearer than the baseline is held for a person. Likely category:
`budget_allocation` or `finding_quality`.
