# Response and handoff

Account for every source item; a short human summary can point to a fuller local handoff. Preserve IDs/URLs, source snapshot, current verification target, evidence, and outcomes.

## After verification

| ID / source | Assessment | Evidence | Proposed action |
| --- | --- | --- | --- |
| Original comment ID or local label | VALID / INVALID / UNCERTAIN / NEEDS CLARIFICATION | Current code location or command/result under the reported conditions | Authorized fix, concrete proposal, evidenced rejection, missing evidence, or specific clarification |

Include proposed work chunks, finding IDs, dependencies, and file ownership. Follow the [fix-launch decision](SKILL.md#fix-launch-decision), preserving triage-only scope and existing authorization. Keep overrides separate from assessments, recording the user instruction and desired change.

## Final result

Report each item's disposition: implemented and verified; changed with validation incomplete; blocked/uncertain/needs clarification; invalid with evidence; user-excluded/outside scope; or user override implemented. Do not omit unresolved items to make the table binary. A single item may be a short evidenced paragraph.

Summarize integrated checks once with actual commands/results, tested tree/commit, raw evidence locations, and unavailable checks. Distinguish deterministic fixture evidence, live Harbor pilot, full evaluation, and provider checks. Do not imply any test, commit, push, reply, or resolution happened without confirmation.

Follow the [publication and response handoff](SKILL.md#publication-and-response-handoff) within existing authorization. Include exact approved publication scope and target when a publication decision is needed. Record pending/declined decisions and failed publication accurately.

For `respond-pr-feedback`, preserve original REST/node comment IDs, review IDs, thread IDs, URLs, root reply-parent IDs, original locations, source head SHA, and base/fork repository identity. Pass assessments/overrides separately from implementation outcomes, tested tree/commit and checks, outstanding issues, and local/committed/remote state. A valid claim alone is not a fixed claim; a local commit alone is not a published fix.
