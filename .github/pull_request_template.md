<!-- Replace placeholders and remove these comments. Keep it short, but include any
context a reviewer can't get from the diff, issue or repository; link details instead
of repeating them. A bugfix gets at most 3 sentences (trigger, wrong behavior, fixed
behavior); other changes at most 5 (purpose and result). Describe the final change,
not how you got there.
-->
## Summary

<!-- Add the smallest visual that makes the point, next to the text it supports:
pseudocode for logic, a call tree for control flow, a shallow file tree for file
responsibilities, Mermaid for interactions or data flow, or a diff sketch for a
changed shape.
-->
[Summary]

<!-- Full issue URL. Use Closes only if this PR fully resolves it; otherwise Related to.
With no issue, say why briefly.
-->
Issue: [Closes / Related to + full GitHub issue URL, or no linked issue — brief reason]

## Added

<!-- Keep all three change sections. Use short bullets, or None. -->
- [New behavior or capability, or None.]

## Changed

- [Existing behavior that changes, or None.]

## Removed

- [Behavior or code removed, or None.]

## Verification steps

- [ ] Ran the relevant documented repository checks and recorded the outcome below, or explained why they are not applicable.
- [ ] For custom code, recorded the Harbor-first evidence required by `CONTRIBUTING.md`, or explained why it is not applicable.

<!-- Record only checks run on the final change: exact commands or steps, what you
observed, and what each check shows. Keep fixture tests separate from live reviewer or
judge runs and benchmark evaluations; for benchmark results, give the review cases,
attempt count (k), reviewer and judge settings, and where results are kept. Disclose
failed or skipped checks and why. If later commits weren't rechecked, name the
revision each result applies to.
-->
Verified revision: [commit SHA]

- `[Exact command]` — [Observed result; behavior verified.]
- [Manual steps, if performed] — [Observed result; behavior verified.]
- [Failed or skipped check, if any] — [Reason and remaining uncertainty.]

## Review context

<!-- Optional; remove if the summary is enough. Include only context useful for
review or history: a non-obvious decision and why, an invariant, compatibility needs,
or a risk and its rollback limits.
-->
- [Relevant context]
