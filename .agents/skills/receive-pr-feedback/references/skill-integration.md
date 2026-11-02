# Focused supporting guidance

Use these local skills only for the actual claim or approved fix, when the files exist. Repository guidance and [VERIFICATION.md](../VERIFICATION.md) remain sufficient if an optional skill is unavailable. Reading a supporting skill does not expand scope or authorization.

| Need | Skill | Use |
| --- | --- | --- |
| Difficult bug or performance regression | [diagnosing-bugs](../../diagnosing-bugs/SKILL.md) | Reproduce conditions, test hypotheses, and verify the original failure after fixing |
| Test-first fix | [tdd](../../tdd/SKILL.md) | Add a meaningful behavior test through the relevant converter/scorer/adapter boundary |
| Test quality | [test-audit](../../test-audit/SKILL.md) | Investigate assertion quality, redundancy, or missing meaningful coverage when requested or needed |
| Module/interface decision | [codebase-design](../../codebase-design/SKILL.md) | Evaluate a necessary interface change without launching a broader redesign |
| Domain term or decision | [domain-modeling](../../domain-modeling/SKILL.md) | Resolve glossary/ADR changes needed for authorized work |
| Independent branch review | [code-review](../../code-review/SKILL.md) | Use a fixed comparison/spec when a broader review is requested; item verification does not require a second full review |

Read [AGENTS.md](../../../../AGENTS.md), [GLOSSARY.md](../../../../GLOSSARY.md), and relevant [ADRs](../../../../docs/adr/) before domain work. Harbor owns evaluation execution through native controls and supported extension points. Custom code requires source, documentation, and test inspection at `/Users/ka/github/harbor` plus a recorded precise gap. Review the [README](../../../../README.md) for current delivery scope; planned converters, adapters, and tasks are not proof that they exist.

For GitHub issue/spec operations, read [the tracker guide](../../../../docs/agents/issue-tracker.md); before assigning triage roles read [the label mappings](../../../../docs/agents/triage-labels.md). Feedback assessments are separate from issue labels. Selected PR feedback handling does not turn PRs into the repository's general triage request surface. This reference does not itself authorize issues, comments, labels, replies, or thread resolution.
