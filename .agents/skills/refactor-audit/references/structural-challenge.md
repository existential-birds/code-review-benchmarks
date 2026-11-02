# Structural challenge

Use when initial findings are local deletions, a promising change is dismissed
as risky, or a replacement may merely relocate complexity. Re-read the strongest
lead through actual callers rather than assuming every example needs a change.

## Redraw the interface

Start from the caller's desired outcome. Sketch the before/after call and trace
the responsibilities it would remove across the workflow.

| Lead | Question to answer from source |
| --- | --- |
| Custom suite runner, retries, parallel loops, or result aggregation | Which relevant Harbor source, docs, and tests prove this is already supported? What exact benchmark-specific gap remains? |
| Orchestrator → manager → one-entry strategy → adapter | Which layer adds policy, variation, isolation, or a public contract? Can the supported adapter operation own the rest? |
| Several DTOs/envelopes for one value | Which representation changes meaning or captures immutable review context? Keep that guarantee while removing identity translations. |
| Helpers forcing a follow-up query | Can the authoritative operation return its committed result/version rather than making callers reconstruct it? |
| Repeated setup and cleanup | Can one scoped operation own acquisition, validation, disposal, and failure cleanup? |
| Several flags and counters | Which transitions need independent state? Can one owner prevent invalid trial/result combinations? |
| Similar benchmark pipelines | What mechanics are shared, and which reference data, scoring rules, and denominators must remain distinct? |
| Registries, flags, plug-ins, factories | Which real callers use each variation, including CLI/configuration and dynamic consumers? |

Inspect complete chains, including unchanged code and consumers outside measured
directories. Duplication tools locate leads; unsupported languages or generated
code cannot silently stand in for examined coverage.

## Challenge the replacement

- Count the full replacement and migrated callers. Which decision owner, state,
  concept, or maintenance path disappears? A new helper alone does not prove it.
- Could native Harbor configuration, a supported extension point, or a plain
  function serve the actual callers? A new framework needs demonstrated variation.
- Must callers still coordinate the same partial results or flags? If so, the
  interface may remain at the wrong boundary.
- Does the reduction come from formatting, renames, docs, or excluded files while
  runtime complexity remains? Report those separately.

## Prove necessary differences

Before merging checks or pipelines, name what would break if they shared an
implementation: immutable inputs captured before I/O; retryable reads versus an
uncertain accepted mutation; reference agreement versus bug precision; successful
empty findings versus failed trials; benchmark-specific scoring versus Harbor
execution. Preserve the obligations and verify their actual boundary.

If they prevent simplification, retain the code and cite evidence. “Security,”
“one caller,” “tests pass,” “too many files,” “risky,” and a small LOC estimate
are observations requiring investigation, not dispositions by themselves.

## Earn the stopping decision

Reassess leads after each integrated and verified batch. Give remaining leads an
evidence-backed disposition: unnecessary, unsupported, blocked, or outside the
user's scope/effort limit. If a limit stops discovery, name unexamined workflows.
Neither a deletion list nor a findings quota substitutes for the structural pass.
