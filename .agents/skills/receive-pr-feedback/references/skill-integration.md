# Focused supporting guidance

Use these skills only for the actual claim or approved fix, when they are installed from `skills-lock.json`. Repository guidance and [VERIFICATION.md](../VERIFICATION.md) remain sufficient if an optional skill is unavailable. Reading a supporting skill does not expand scope or authorization.

| Need | Skill | Use |
| --- | --- | --- |
| Difficult bug or performance regression | [diagnosing-bugs](../../diagnosing-bugs/SKILL.md) | Reproduce conditions, test hypotheses, and verify the original failure after fixing |
| Test-first fix | [tdd](../../tdd/SKILL.md) | Add a meaningful behavior test through the relevant converter/scorer/adapter boundary |
| Test quality | [test-audit](../../test-audit/SKILL.md) | Investigate assertion quality, redundancy, or missing meaningful coverage when requested or needed |
| Module/interface decision | [codebase-design](../../codebase-design/SKILL.md) | Evaluate a necessary interface change without launching a broader redesign |
| Domain term or decision | [domain-modeling](../../domain-modeling/SKILL.md) | Resolve glossary changes needed for authorized work |
| Independent branch review | [code-review](../../code-review/SKILL.md) | Use a fixed comparison/spec when a broader review is requested; item verification does not require a second full review |

Read [AGENTS.md](../../../../AGENTS.md), [GLOSSARY.md](../../../../GLOSSARY.md), and [CONTRIBUTING.md](../../../../CONTRIBUTING.md) before domain work. Harbor owns evaluation execution through native controls and supported extension points. Custom code requires source, documentation, and test inspection of the pinned Harbor checkout plus a recorded precise gap. Review the [README](../../../../README.md) for current scope.

Feedback assessments are separate from issue labels. This reference does not itself authorize issues, comments, labels, replies, or thread resolution.
