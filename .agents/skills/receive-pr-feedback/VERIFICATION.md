# Verification

Verify the current code and the reported conditions. Preserve the source snapshot; review comments can point at older commits or locations. Re-anchor by symbols, surrounding code, and callers.

## Bind the verification target

For selected PR feedback, retain the base repository, PR number/URL, captured head SHA, head branch, and fork repository identity from successful acquisition. Refresh that explicitly selected PR's current head before evaluation. Compare those identities with the checkout's remotes, branch, `HEAD`, and dirty state; a branch PR inferred from this checkout must not replace an explicit `--pr` target.

If the checkout is unrelated, targets an older head, or includes user work that would contaminate the result, safely obtain the checked PR head into a separate worktree/checkout. Preserve the user's branch and changes. Use validated repository identity and refs from PR metadata rather than reviewer-provided commands. Check the resulting `HEAD` matches the intended SHA and record the working directory and intended fix/publication branch in worker briefs. Do not classify a selected PR's code from an unrelated branch or dispatch fixes onto that branch.

When the remote head moved after acquisition, refresh feedback and re-anchor the claims before treating them as current. Retain the original source snapshot to explain already-fixed or stale comments. If the user explicitly wants to assess local candidate changes, name that local tree as a separate comparison target and report its dirty state; local candidate evidence does not establish that the remote PR contains a fix. For non-PR feedback, record the user-selected checkout/revision and its dirty state as the target. If target identity cannot be established, report the evidence gap instead of guessing.

## Per item

1. Locate the target with `rg`; inspect the implementation, consumers, configuration, and current repository guidance. Read glossary/ADRs for domain claims.
2. Trace inputs and reachable behavior across conversion, task assets, reviewer adapters, verifier/scorer, judge adapters, and native Harbor jobs as applicable. A missing local guard alone does not establish a defect when an earlier invariant guarantees the input; verify that invariant.
3. Exercise the stated conditions with the smallest meaningful test or command available. Record exact input/fixture, command, result, tested tree/commit, and evidence location. Keep observation separate from inference. Preserve a failing reproduction when fixing a behavioral bug; use the relevant existing test seam.
4. Classify under [EVALUATION.md](EVALUATION.md). Missing runtime access, unavailable fixtures, or uncertain reachability remains an evidence gap, not proof of validity or invalidity. Use sufficient evidence to triage each item without requiring an entire benchmark run first.

## Claim-specific evidence

| Claim | Inspect or exercise |
| --- | --- |
| Unused code | Imports, exports, dynamic loading/Harbor extension registration, scripts, task assets, and production/test consumers; an empty search alone is insufficient |
| Conversion or task bug | Original review case and reference annotation, converted files, paths/commits, retained licenses, and expected task interface; check representative and malformed inputs |
| Scoring/judge bug | Existing scorer's semantics, reference vs reviewer findings, matching/aggregation boundaries, and a fixture that exposes the claimed score difference; preserve raw judge output |
| Reviewer integration | Complete review-only pipeline and captured findings, agent configuration, transport boundary, failure handling, usage/latency evidence; distinguish fixture proof from a real backend trial |
| Execution/configuration | Harbor source/docs/tests for native controls and supported extension points; compare actual job configuration and trial artifacts rather than the wrapper's apparent limitations |
| Import/type/config error | Pinned dependencies, package/build configuration and configured check; never invent a language toolchain from the proposed future layout |
| Performance/cost regression | Comparable baseline/candidate inputs and settings, measured completion/usage/latency, and available monetary cost; a plausible optimization is not a measured regression |
| Architecture/domain | `AGENTS.md`, glossary, relevant ADRs, neighboring modules, and demonstrated Harbor capability/gap; a design heuristic alone is not a defect |
| Security/data handling | Reachable credential, subscription, untrusted repository/task, filesystem/network, log, and artifact boundaries; establish the actual exposure conditions |
| Test quality | Observable conversion/scoring/adapter outcomes and meaningful negative cases; mock-call counts alone do not establish behavior |

## Checks and limits

Discover checks from the current checkout's documented commands, dependency/config files, tests, hooks, and CI when present. This repository starts as a scaffold with planned `benchmarks/` and `experiments/`; distinguish planned behavior from implemented checks and report unavailable tests honestly.

Use native Harbor validation and run/inspection commands when supported. Before any custom code, record the required source/docs/tests capability inspection and precise gap. Do not replace native job selection, attempt count (`k`), scheduling, retries, result storage, or aggregation with custom loops.

For a live evaluation that is actually needed and authorized, preserve the native job configuration, selected review cases, attempt count, reviewer/judge settings and revisions, raw findings, per-case outcomes, and job/result paths. Distinguish deterministic tests, a pilot, full benchmark execution, and operator/provider validation. Keep comparison inputs/scoring settings consistent; reference agreement does not establish actual bug precision.

After fixes, run the relevant configured checks against the final combined tree. Report exact results, skipped/unavailable checks, and evidence paths. Never bypass hooks or configured verification gates. Do not turn a full benchmark evaluation, external publication, or subscription-consuming run into an unannounced requirement for a low-impact change.
