<!-- Replace placeholders and remove guidance comments before submitting.
Keep the description as concise as possible and quickly readable by humans.
Agents use it to inform code review: retain all context they cannot get from the
diff, linked issue, or repository, plus anything that materially helps review.
Link existing details instead of repeating them.
Bugfix: at most 3 sentences covering the trigger, incorrect behavior, and fixed behavior.
Other change: at most 5 sentences explaining its purpose and resulting behavior.
Describe the final change; omit work chronology and abandoned approaches.
-->
## Summary

<!-- Pick the smallest view that makes the key point clear:
- Logic or algorithm: pseudocode.
- Runtime control flow: call tree.
- UI structure: component tree with relevant state and module boundaries.
- File responsibility or broad refactor: shallow file tree.
- Component interaction, control flow, or data flow: Mermaid.
- Changes to an existing shape: diff sketch matching the topic (components,
  file layout, call tree/stack, or state/control flow).
- Mostly new code, essential ownership/order context, or a copyable target:
  the whole block.
Place each visual next to the short text it supports. Include only the calls,
files, props, states, and boundaries needed to explain the change. Use one or
several views as useful; keep the summary easy to scan.
See .agents/skills/pr/SKILL.md for examples.
-->
[Summary]

<!-- Link the GitHub issue using its full URL. Use Closes only when this PR fully resolves it;
otherwise use Related to. If there is no issue, give a brief reason.
-->
Issue: [Closes / Related to + full GitHub issue URL, or no linked issue — brief reason]

## Added

<!-- Keep all three change sections. Use concise bullets, or None. -->
- [New behavior or capability, or None.]

## Changed

- [Existing behavior that changes, or None.]

## Removed

- [Behavior or code removed, or None.]

## Verification steps

- [ ] Ran the relevant documented repository checks and recorded the outcome below, or explained why they are not applicable.
- [ ] For custom code, recorded the Harbor-first evidence required by `CONTRIBUTING.md`, or explained why it is not applicable.
- [ ] Ran `$test-audit` when this PR includes test changes and recorded the outcome below, or explained why it is not applicable.

<!-- Record verification actually performed on the final change. Give exact commands
or reproducible manual steps, observed results, and the correctness each check establishes.
Include relevant environment/backend details and brief evidence, not full logs.
Separate deterministic fixture evidence from live reviewer/judge runs and broader
benchmark evaluations. For benchmark results, record the selected review cases,
attempt count (k), reviewer/judge settings, and retained result location.
Follow CONTRIBUTING.md and repository verification requirements; disclose failed or skipped
required checks, their reasons, and the remaining uncertainty.
Do not present planned checks as passed. Identify earlier evidence by its revision
when later changes have not been rechecked.
-->
Verified revision: [commit SHA]

- `[Exact command]` — [Observed result; behavior verified.]
- [Manual steps, if performed] — [Observed result; behavior verified.]
- [Failed or skipped check, if any] — [Reason and remaining uncertainty.]

## Review context

<!-- Optional: remove this section if the summary and bullets are sufficient.
Capture only context useful for review or future history: a non-obvious decision and
its reason, a relevant code path/invariant, compatibility or migration requirements,
or a concrete risk and rollback limitation. Link supporting evidence where useful;
do not repeat the diff or linked documents.
-->
- [Relevant context]
