# Glossary

Terms for measuring changes to Daydream's code-review behavior against benchmark
cases.

**Benchmark**:
A collection of review cases and the reference annotations and scoring rules
used to evaluate findings on those cases.

**Review case**:
A particular proposed code change and its surrounding repository context,
presented to a reviewer for evaluation.
_Avoid_: Sample

**Catalog**:
The Harbor tasks a converter generates from a benchmark, one per review case.

**Reviewer**:
The complete system being evaluated for producing code-review findings,
including its model, orchestration, and filtering behavior.
_Avoid_: Model, when referring to the complete system

**Candidate build**:
A Daydream commit built for scoring without changing the pinned build.

**Finding**:
A reviewer's reported issue concerning a proposed code change.

**Reference finding**:
A benchmark annotation describing an issue against which reviewer findings
are compared. The annotations need not exhaust every valid issue in a case.

**Judge**:
The system that assesses semantic agreement between a reviewer finding and a
reference finding during scoring.
_Avoid_: Reviewer, when referring to the scoring role

**Oracle**:
A reviewer stand-in that submits fixed findings, used to check the judge and
scoring without running Daydream.

**Dev and held-out splits**:
The review cases used to tune Daydream, and the cases kept back to confirm a
change. Tuning never sees the held-out split.

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

**Round**:
One job's complete pass over a catalog, with one trial for each review case.
_Avoid_: Attempt count, whose trials pool into one job
