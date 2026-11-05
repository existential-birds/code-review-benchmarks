# Verification

Check the current code under the reported conditions. Comments can point at older
commits, so re-anchor each one by symbol, surrounding code and callers.

## Pin the target

For PR feedback, keep the base repository, PR number and URL, head SHA, head branch
and fork identity from the fetch, and refresh the selected PR's head. Compare them
with the checkout's remotes, branch, `HEAD` and dirty state. An explicit `--pr` always
wins over the current branch's PR.

If the checkout is unrelated, behind, or holds user work that would skew the result,
check out the PR head in a separate worktree using refs from PR metadata, not
reviewer text, and confirm `HEAD` matches. Never classify or fix a PR's code from an
unrelated branch. If the remote head moved, refresh the feedback and re-anchor. Local
candidate changes are a separate target and don't show that the PR contains a fix.
For non-PR feedback, record the chosen revision and its dirty state. If the target
can't be established, report that instead of guessing.

## Per item

1. Find the code with `rg` and read the implementation, consumers, configuration and
   guidance.
2. Trace the input through conversion, task assets, reviewer adapters, the verifier
   and scorer, judge adapters and Harbor jobs as relevant. A missing local guard
   isn't a defect if an earlier invariant covers it; check the invariant.
3. Exercise the reported conditions with the smallest test or command, recording the
   input, command, result, tested commit and evidence location. Keep a failing
   reproduction for a behavioral bug.
4. Classify under [EVALUATION.md](EVALUATION.md). Missing access or unclear
   reachability is an evidence gap, not proof either way. Triage doesn't need a full
   benchmark run.

## What to check per claim

| Claim | Inspect or exercise |
| --- | --- |
| Unused code | Imports, exports, dynamic loading and Harbor registration, scripts, task assets, and production and test consumers |
| Conversion or task bug | The original review case and reference annotation, converted files, paths and commits, licenses, and the task interface, with representative and malformed inputs |
| Scoring or judge bug | The scorer's semantics, reference versus reviewer findings, matching and aggregation, and a fixture that shows the claimed score difference, keeping raw judge output |
| Reviewer integration | The full review-only pipeline and captured findings, agent configuration, transport, failure handling, and usage and latency, separating fixture proof from a real trial |
| Execution or configuration | Harbor's source, docs and tests, compared with the actual job configuration and trial artifacts |
| Import, type or config error | Pinned dependencies, package configuration and the configured check |
| Performance or cost | Comparable baseline and candidate inputs and settings, with measured completion, usage, latency and cost |
| Architecture or domain | `CONTRIBUTING.md`, the glossary, neighboring modules and Harbor's demonstrated capabilities |
| Security or data handling | Reachable credential, subscription, untrusted repository, filesystem, network, log and artifact boundaries, and the real exposure conditions |
| Test quality | Observable outcomes and meaningful negative cases; mock-call counts alone prove nothing |

## Checks

Use the checks this checkout documents. Run a live evaluation only when needed and
authorized, keeping its configuration, review cases, attempt count, settings, raw
findings and job paths. After fixes, run the relevant checks on the final combined
tree and report exact results and skips. Never bypass hooks, and don't make a full
benchmark or paid run an unannounced requirement for a small change.
