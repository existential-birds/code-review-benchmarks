# Structural challenge

Use this when the findings are only local deletions, a promising change is dismissed
as risky, or a replacement might just move complexity around. Re-read the strongest
lead through its callers.

## Redraw the interface

Start from what the caller wants, sketch the call before and after, and trace what
the change removes.

| Lead | Question to answer from source |
| --- | --- |
| Custom suite runner, retries, parallel loops or result aggregation | Which Harbor source, docs and tests show this is already supported, and what benchmark-specific gap remains? |
| Orchestrator, manager, single-entry strategy, adapter | Which layer adds policy, variation, isolation or a public contract? Can the adapter own the rest? |
| Several wrappers for one value | Which one changes meaning or captures immutable review context? Keep that and drop the plain copies. |
| Helpers that force a follow-up query | Can the operation return its committed result instead of making callers rebuild it? |
| Repeated setup and cleanup | Can one scoped operation own setup, validation, disposal and failure cleanup? |
| Several flags and counters | Which transitions need independent state? Can one owner rule out invalid trial and result combinations? |
| Similar benchmark pipelines | Which mechanics are shared, and which reference data, scoring rules and denominators must stay separate? |
| Registries, flags, plug-ins, factories | Which real callers use each variation, including CLI, configuration and dynamic consumers? |

Read whole chains, including unchanged code and consumers outside the measured
directories.

## Challenge the replacement

- Count the full replacement and migrated callers. Which owner, state, concept or
  maintenance path disappears? A new helper alone proves nothing.
- Could Harbor configuration, a supported extension point or a plain function serve
  the real callers? A new framework needs real variation.
- Must callers still coordinate the same partial results or flags? Then the boundary
  may still be wrong.
- Does the reduction come from formatting, renames, docs or excluded files? Report
  that separately.

## Prove necessary differences

Before merging checks or pipelines, name what sharing would break: inputs captured
before I/O, retryable reads versus an uncertain accepted write, reference agreement
versus bug precision, successful empty findings versus failed trials, benchmark
scoring versus Harbor execution. If those differences block a simplification, keep
the code and cite the evidence. "Security", "one caller", "tests pass", "risky" or a
small line estimate are observations to investigate, not conclusions.

## Stop with evidence

Reassess leads after each verified batch, and give each remaining lead a disposition:
unnecessary, unsupported, blocked, or outside scope. If a limit stops the search, name
what wasn't examined.
