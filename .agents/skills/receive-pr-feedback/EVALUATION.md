# Evaluation rules

Track whether a claim is valid separately from whether it's fixed. Keep reviewer
order, IDs, URLs and snapshot identity throughout.

| Assessment | Evidence required | Action |
|---|---|---|
| VALID | The defect holds at the current target, with its conditions and impact established | Fix within scope, or propose a concrete fix if implementation isn't authorized |
| INVALID | Evidence disproves the claim under its stated conditions, or shows a false premise | Reject with code or command evidence |
| UNCERTAIN | Evidence is incomplete, contradictory or stale, or reachability is unclear | Investigate further and name the missing evidence |
| NEEDS CLARIFICATION | Ambiguity affects the intended behavior or the fix | Ask the specific question and continue other work |

For a valid finding, record one of: implemented and verified, changed but not fully
validated, blocked, outside scope, or excluded by the user. A blocker doesn't make a
claim invalid, a proposed patch isn't a fix, and a skipped test proves nothing. A
user override changes the work set, not the assessment.

## Evidence for rejecting a claim

- A symbol called unused has a live consumer. Check whether the claim was about the
  whole symbol or one branch inside it.
- The missing guard exists earlier on the reachable path.
- Reproducing the reported conditions shows correct behavior. A test under different
  conditions doesn't disprove the bug.
- The suggestion breaks a documented benchmark or Harbor invariant. Cite it, and check
  whether the underlying defect needs another fix.
- The comment targets an older snapshot that's already corrected.

An empty search, one non-reproducing run, a convention or an opinion can't show a
claim is invalid, and missing evidence can't show it's valid. Record uncertainty
instead.

## Scope and order

Keep source numbering even when execution order changes, and say why. Whether a
defect predates the PR is context, not a reason to skip or expand authorized work.
When every finding is authorized, pursue every confirmed one and record what stays
blocked. State findings and results plainly, without performative agreement.
