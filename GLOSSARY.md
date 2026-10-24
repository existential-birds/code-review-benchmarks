# Code Review Evaluation

The language used to discuss measuring changes to Daydream's code-review
behavior against benchmark cases.

## Language

**Benchmark**:
A collection of review cases and the reference annotations and scoring rules
used to evaluate findings on those cases.

**Review case**:
A particular proposed code change and its surrounding repository context,
presented to a reviewer for evaluation.
_Avoid_: Sample

**Reviewer**:
The complete system being evaluated for producing code-review findings,
including its model, orchestration, and filtering behavior.
_Avoid_: Model, when referring to the complete system

**Finding**:
A reviewer's reported issue concerning a proposed code change.

**Reference finding**:
A benchmark annotation describing an issue against which reviewer findings
are compared. The annotations need not exhaust every valid issue in a case.

**Judge**:
The system that assesses semantic agreement between a reviewer finding and a
reference finding during scoring.
_Avoid_: Reviewer, when referring to the scoring role

**Baseline**:
A recorded evaluation of Daydream that serves as a reference for assessing
later changes.

**Pilot**:
A small initial evaluation used to validate execution and scoring before a
larger evaluation.

**Trial**:
One reviewer attempt on a review case, together with its scoring outcome.

**Attempt count (k)**:
The number of independent trials requested for each selected review case and
reviewer in an evaluation run.
_Avoid_: Rounds
