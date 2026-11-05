# Response and handoff

Account for every source item, keeping IDs, URLs, the source snapshot, the
verification target, evidence and outcomes.

## After verification

| ID / source | Assessment | Evidence | Proposed action |
| --- | --- | --- | --- |
| Original comment ID or local label | VALID / INVALID / UNCERTAIN / NEEDS CLARIFICATION | Current code location, or command and result under the reported conditions | Authorized fix, concrete proposal, evidenced rejection, missing evidence, or specific question |

List proposed work chunks with finding IDs, dependencies and file ownership, then
follow the [fix-launch decision](SKILL.md#fix-launch-decision).

## Final result

Give each item's outcome: implemented and verified; changed but not fully validated;
blocked, uncertain or needs clarification; invalid, with evidence; excluded or
outside scope; or user override implemented. Summarize checks once with commands,
results, tested commit and evidence locations, keeping fixture tests, live pilots,
full runs and provider checks distinct. Never imply a test, commit, push, reply or
resolution that didn't happen.

For [respond-pr-feedback](../respond-pr-feedback/SKILL.md), pass the original comment,
review and thread IDs, URLs, reply parents, locations, source head SHA and repository
identity, plus assessments, outcomes, tested commit, checks and whether each fix is
local, committed or pushed. A valid claim isn't a fixed one, and a local commit isn't
a published fix.
