# AACR pilot validation

The pilot evidence below is a historical capture-disabled run. The subsequent
user-requested local JSONL capture change is documented in the benchmark README;
it applies to newly generated task assets and fresh jobs. Existing pilot results
and their native ATIF trajectories remain intact.

Validation of [issue #4](https://github.com/existential-birds/code-review-benchmarks/issues/4).
The pilot, attempt and concurrency jobs ran from a worktree based on
`1dd5a45b1c3f6855ddbb3bac71ce27f2c4cf3618`, before that work was committed as
`a83796c9530882bc87b8328369fc9e11cbaee0bb`. Their frozen scorer code equals the
final source; the frozen reviewer entrypoint differs from it only by capture
propagation. The credential refresh job ran from a catalog generated at
`8030e2a54be9f5b24e6f7b94914d85114ffe8e6f`.
The serial pilot passed: **10/10 completed and scored, zero errors and retries**.
The independent-attempt, concurrency and live credential refresh checks passed.
Full conversion accounted for all 196 cases; all 185 generated tasks passed the
final catalog audit. Eleven pinned heads are unavailable.

## Inputs and implementation

The input is AACR revision `ee9d7fef70ef31acea2ff7ae316ce0a9575fe4df`,
with `positive_samples.json` SHA-256
`02c8561f1c3994a26f427d479b2a3f6e6c8e6858161fbc903b45248fa2cb2097`:
196 unique review cases, 1,506 reference findings, ten languages.
Daydream source is `5bae1ac09918ecf8d095aaf6d283235ca1ad83a8`; wheel SHA-256
is `923075dff2be5e6d5b2bdc670eb5e247fc86efc391ae5d6c23eee35e4ca63d92`.
All 391 installed Daydream package files were compared with that exact wheel
before conversion. Harbor source/executable is pinned to
`6099b8cf2bfbbb3e7ff1eb2163bfc4524dbd9ba5` (0.24.0).
The host converter uses Python 3.14.3; the pinned Harbor CLI uses Python 3.13.5.

The converter now generates the full catalog by default, validates source and
case identities, refuses overwrites, and records each attempted conversion.
It fetches exact base/head commits into isolated contexts and deepens shallow
history until the merge base is safe. A real merged-history regression covers
the case where an initial shallow merge base is an older ancestor.
Both reviewer image variants install native Git LFS filters before copying the
materialized checkout. A real LFS fixture demonstrated and now prevents dirty
pointer/index admission failures. The Oracle solution remains limited to its
exact pinned Laravel case; Daydream tasks contain no Oracle solution.

Harbor owns the job plan, selection, attempts, concurrency, deadlines, retries,
results, aggregation and viewer. New custom scoring is one supported
`uv-script` metric: it sums four validated integer counts and calls unchanged
AACR `_compute_summary`. The operational commands and inspected Harbor
capabilities are documented in [the benchmark README](../benchmarks/aacr/README.md#pilot).

The pilot catalog has the selected ten cases plus excluded Laravel control:
11 generated cases, 53 references. Native plan resolution excludes that control.
The proposed Node.js PR #56185 head
`b2255442712cb6db83d112deb6ba61197d06a5f3` is unavailable from its declared
repository. Checkmate PR #1897 replaces it as the JavaScript case, giving the
pilot 52 references across ten languages. The failed original preparation is
retained in `artifacts/aacr/issue4-pilot-tasks-node-unavailable/catalog.json`.
Every selected task has its annotated paths inside the actual native review
diff, verified clean exact revisions and frozen reviewer/scorer assets.

## Full catalog

Default conversion attempted all **196 cases / 1,506 references**:
**185 generated cases / 1,419 references**, and **11 unavailable heads / 87
references**. The converter exited **1**, as required when any case fails.
Every failure is a native Git `upload-pack: not our ref` response for the pinned
head from the declared repository; no revision was silently substituted.

| Unavailable review case | Pinned head | References |
|---|---|---:|
| astral-sh-uv-11088 | `ed57db2b34c2a7a955b2dc76583dce7ace02e628` | 6 |
| clickhouse-clickhouse-74070 | `cc962559c45fcee9706d86c6425d48e794676a67` | 16 |
| clickhouse-clickhouse-85266 | `fd6ba0c19048b6a546188661d1092c6c76a72796` | 5 |
| comfyanonymous-comfyui-7952 | `4936d01872b8719ede33f9a11dc8e898c33d39e4` | 5 |
| comfyanonymous-comfyui-9560 | `cfc312296c0b9f255bba6b4b2789ac7bbc66a7cb` | 6 |
| keycloak-keycloak-35645 | `460f8008f86d3fa8f62da63e26d8bdc306af60b2` | 7 |
| keycloak-keycloak-36457 | `79dbba9b179015cfd599ebac63112e34c9d724af` | 9 |
| keycloak-keycloak-37465 | `2373a483fdeead9f1b5e815015349c6871de8c3a` | 7 |
| keycloak-keycloak-37504 | `21d4538b6f69a47dbd25571ea39a5d2a6fa788ef` | 21 |
| keycloak-keycloak-41672 | `1463502078ae277b3613e02429ecafde921b892b` | 1 |
| nodejs-node-56185 | `b2255442712cb6db83d112deb6ba61197d06a5f3` | 4 |

`catalog.json` retains the base, head, identity, reference count and complete
error for every attempted case. The separate validation audit checks native Task
admission, exact source/reference fidelity, clean base/head, safe merge base,
native patch/hash, frozen build/runtime assets and copied scorer/entrypoint
identities. **All 185 generated tasks passed**, with zero validation failures.
Stable native discovery and `k=1` expansion resolve exactly those 185 distinct
names, with no unavailable/staging extras, retries zero and the protected
`DaydreamVerifier` intact.

Native lint checked every generated task exactly once across nine batches and
exited 1 in each batch. All **753 findings** are retained and classified:
370 external credential binds, 370 writable credential binds, ten tracked
ClickHouse thrift links and three tracked Linera dependency links. There are
zero unresolved classifications. Credential binds intentionally retain separate
writable native stores for refresh; no lint-ignore option was used.
Full baseline reviewer/judge inference is outside this delivery.

Linera's three tracked symlinks point to absent `node_modules`/build output;
its selected pilot trial built and scored successfully with those links intact.
ClickHouse's tracked thrift links point under an uninitialized Git submodule.
These source links are preserved and lint findings retained; submodule and
dependency content availability is not established. Static task admission does
not prove Docker build or live execution for the other full-catalog cases.

## Serial pilot

Native job: `jobs/aacr-issue4-pilot-20261029-a`, composed from
`experiments/aacr-baseline.yaml` and `experiments/aacr-pilot.yaml`, with
`k=1`, concurrency 1, automatic retries 0, one reviewer, one metric and the
protected `DaydreamVerifier`. Native print-config was rehydrated with `JobConfig`
and resolved through `JobPlan`; the ten-case native dry run passed.

The main score excludes the independent-attempt and concurrency checks below.
All finalized findings, full judge responses and native review evidence are
inspected and reconciled against raw comment flags, per-trial rewards and native
pooled metrics. Reference agreement does not establish actual bug precision;
unmatched findings are not established false positives.

| Review case | References | Findings | Matched findings | Covered references | Judge calls | Reviewer seconds |
|---|---:|---:|---:|---:|---:|---:|
| Checkmate #1897 (JavaScript) | 9 | 1 | 1 | 1 | 1 | 208.252 |
| Cline #2347 (TypeScript) | 9 | 4 | 1 | 2 | 3 | 274.741 |
| Dbeaver #37564 (Java) | 5 | 2 | 1 | 1 | 2 | 96.638 |
| Ragflow #6691 (Python) | 6 | 1 | 0 | 0 | 1 | 129.597 |
| Linera #4639 (Rust) | 6 | 1 | 1 | 1 | 1 | 91.287 |
| LVGL #8129 (C) | 5 | 0 | 0 | 0 | 0 | 152.638 |
| PowerToys #40814 (C#) | 3 | 0 | 0 | 0 | 0 | 62.117 |
| Ollama #8938 (Go) | 2 | 0 | 0 | 0 | 0 | 66.874 |
| OpenCV #26815 (C++) | 4 | 0 | 0 | 0 | 0 | 57.392 |
| Symfony #61601 (PHP) | 3 | 0 | 0 | 0 | 0 | 48.420 |
| **Pooled** | **52** | **9** | **4** | **5** | **8** | **1,187.955** |

The native pooled metric is precision `4/9 → 0.444`, recall `5/52 → 0.096`,
balanced score **0.158**, using unchanged AACR rounding and summary behavior.
Cline's one matched finding covers two references. Dbeaver's two duplicate
findings cover one reference and count as only one novel generated hit. These
live outcomes preserve the two distinct matching numerators. Five complete empty
reviews contribute their reference denominators and zero findings.

Checkmate #1897 is the multi-file adjudication case. Its review covered three
changed files, its one retained finding links the workflow defaults, the
translation script's output and the application's English fallback, and its native
arbiter and merge phases both completed as non-noop.

Every trial's protected receipt, request UUID, completed terminal state, exact
base/head/merge-base/patch hash, finalized findings hash, and raw comment flags
reconcile with the native reward and aggregate. All eight judge calls completed
through separate Pi OAuth sessions (`openai-codex/gpt-6.1-sol`, SDK 1.4.0,
HTTP 200). Full rationales are retained; upstream's truncated display rationale
was not used as the inspection source.

The job took 2,108.229 seconds (35m 08s), including environment setup and
verification. Reviewer execution ranged from 48.420 to 274.741 seconds, median
93.962; summed execution was 1,187.955 seconds. Callback totals were 6,910,197
input tokens, including 5,444,352 cached, and 69,619 output tokens. Reviewer
API-equivalent estimate: $5.2576254. Judge calls used 4,757 input and 514 output
tokens over 60.227687 seconds; API-equivalent estimate $0.014654. Actual
subscription charges are unknown. Harbor's top-level tokens include sibling
reviews; its per-model backfill covers the root trajectory only.

The packaged default profile and phase model/effort settings are retained,
precision mode is off, and native fanout remains 8. Conditional exploration,
arbiter and merge decisions come from Daydream. A native noop is valid when the
default pipeline decides a phase is unnecessary. Fix/push phases remain absent;
no fixes, commits, publication or training capture are performed by the reviewer.

## Independent attempts

`jobs/aacr-issue4-attempts-20261029-a` used LVGL PR #8129, `k=2`, concurrency 1,
automatic retries 0: **2/2 completed and scored, zero errors or retries**.
Both outputs were complete empty reviews, each against five references; both
match counts and all three pooled agreement rates are zero. No judge inference
was needed for these empty outputs.

The trials have distinct invocation UUIDs
`b68dc530-557e-46a1-b73f-8c39c8e24164` and
`664cc13c-78e3-46d4-8c44-c99d3c6dfea4`, and distinct native sessions
`43db2e1a-ae3f-48b9-8862-4ad7c2597143` and
`59e8b177-1855-40ac-b377-5cbc765086b9`.
Each matched its own protected host receipt, request and execution evidence,
retained the expected analyzed comparison, and started with a disposable native
role home. Only authentication is shared; conversation history is not persisted
in the credential directory. Provider prompt-cache reuse is separately reported
as cached usage.

Reviewer durations were 118.520 and 105.007 seconds. Total reviewer input was
1,762,983 tokens (1,221,760 cached), output 14,865; API-equivalent estimate
$1.503332. Actual subscription charges are unknown.

## Concurrent subset and authentication

`jobs/aacr-issue4-concurrent-20261029-a` selected Checkmate #1897 and Cline #2347
from the pilot catalog, with `k=1`, concurrency 2 and automatic retries 0.
Native print-config/JobConfig/JobPlan resolution proved exactly two distinct
cases, one reviewer, one metric and the protected verifier. Native dry run passed.
Both trials completed and scored: **2/2, zero errors or retries**.

The native reviewer execution intervals were:

| Trial | Started (UTC) | Finished (UTC) |
|---|---|---|
| Checkmate `svc4dPs` | 2026-10-29 23:45:15.294816 | 23:47:38.075778 |
| Cline `agn2nfb` | 2026-10-29 23:45:15.723949 | 23:49:50.435029 |

The Harbor agent-execution stages overlapped by **142.351829 seconds**. Both active reviewer
containers were observed together with distinct trial mounts and only the
canonical Codex credential store. A live separate verifier mount snapshot
contains only Pi credentials and verifier logs, with no Codex store, agent logs
or reviewer artifacts mount. The configured protected verifier admitted each
trial's host receipt and unchanged comparison.

All six retained findings and all five full judge responses were inspected.
Checkmate's language-default finding matched; its token-permission finding did
not match the references. Cline's memory-allocation finding covered two
references; normalization and binary-validation findings did not match, and the
lockfile finding had no annotated path. Separate operational counts are
18 references, six findings, two matched findings and three covered references:
precision .333, recall .167, balanced .222. These counts are excluded from the
ten-case pilot aggregate.

Reviewer execution was 141.442 and 273.286 seconds; combined usage was 1,930,418
input tokens (1,568,256 cached), 21,904 output, API-equivalent estimate $1.6079842.
Five successful Pi OAuth calls used 2,772 input and 331 output tokens over
17.365587 seconds, API-equivalent estimate $0.008854. Subscription charges
remain unknown.

Native store validation passed before and after the concurrent job. Metadata-only
observations retained hashes, modification times, the Codex `last_refresh` value
and Pi expiry. Neither canonical store changed during this check; the reviewer
refresh marker remained 2026-10-22 and Pi expiry remained in the future, so this
job did not exercise live OAuth refresh or persistence.
Native authentication owns refresh and locking; no credentials were copied,
expiry was not forced, and no custom refresh lock, semaphore or retry was added.

## Live credential refresh

`jobs/aacr-issue4-refresh-20261101-a` proves live refresh and persistence for
both canonical stores under concurrency. It was the first credentialed use of the
stores after Pi's expiry passed, from the fresh capture-enabled catalog
`artifacts/aacr/issue4-refresh-tasks`, selecting Checkmate #1897 and Cline #2347
with `k=1`, concurrency 2 and automatic retries 0. Native print-config/JobConfig
resolution and dry run passed. Both trials completed and scored: **2/2, zero
errors, retries or cancellations**.

Metadata snapshots, taken 19 seconds before the live job and 12 seconds after it finished:

| Store | SHA-256 before | SHA-256 after | Refresh metadata before | After |
|---|---|---|---|---|
| Reviewer (Codex) | `ce8d7b78…08ec3` | `ac4a8ee1…ee473` | `last_refresh` 2026-10-22 09:00:05 | 2026-11-01 14:57:45 |
| Judge (Pi) | `e9617d80…404ced` | `46796c15…0a3` | `expires` 2026-11-01 09:01:14 | 2026-11-11 15:06:08 |

The reviewer store was rewritten at 14:57:45, 2.2 seconds after both reviewer
executions started together. It was not rewritten again. The judge store was
rewritten at 15:06:09, about nine seconds after both verifier containers started, while
the judge calls ran. Native `validate_credential_stores` passed afterward.

| Trial | Reviewer started (UTC) | Finished (UTC) | Verifier started | Finished |
|---|---|---|---|---|
| Checkmate `8Dyi6bu` | 14:57:42.915271 | 15:01:48.756687 | 15:02:06.224670 | 15:06:26.837383 |
| Cline `c48RBv2` | 14:57:42.920828 | 15:03:36.851179 | 15:04:00.084729 | 15:06:32.945191 |

Reviewer executions overlapped by **245.835859 seconds**. Native verifier stages
overlapped too. Docker snapshots show each reviewer mounting only the Codex store
plus its own agent, artifact and verifier logs, and each verifier container
mounting only the Pi store and its own verifier logs. All six judge calls
completed through Pi OAuth (`openai-codex/gpt-6.1-sol`, HTTP 200). No job file
contains an authentication failure or `refresh_token_reused`.

All seven findings and six full judge responses were inspected. Checkmate's
language-default finding matched one reference; its token-permission finding did
not. Cline's memory-allocation finding covered two references. Its lossy-encoding,
normalization and binary-validation findings did not match, and its lockfile
finding had no annotated path, so it had no judge call. Counts are 18 references,
seven findings, two matched findings and three covered references: precision .286,
recall .167, balanced .211. These counts are excluded from the ten-case aggregate.
Both trials emitted one native JSONL record that `LocalRecordStore` reads.

Reviewer execution was 243.534 and 350.996 seconds; combined usage was 1,885,684
input tokens (1,515,520 cached), 25,655 output, API-equivalent estimate $1.5131892.
Judge calls used 3,405 input and 397 output tokens over 38.634024 seconds,
API-equivalent estimate $0.01078. Subscription charges remain unknown.

Observed: the hash, modification-time and refresh metadata changes above; their
timing inside the reviewer and judge stages; successful inference after them; and
post-job validation. Inferred, not observed: the exact trigger and writer of each
refresh. The reviewer token was deliberately not decoded, so its expiry is unknown;
pinned Codex refreshes on token expiry or a `last_refresh` older than eight days.
Metadata records only the final write, so it cannot show how many processes
attempted refresh or whether attempts overlapped. No forced rotation or induced
refresh failure was tested; Issue #9's deterministic overlap and rotation tests
remain the synthetic evidence for those paths.

## Error behavior and checks

The extended existing integration fixture exercises the actual installed agent,
configured native verifier and real scorer, with environment and inference
doubles. An actual `asyncio.wait_for` timeout, cancellation, nonzero execution,
judge authentication failure, stale invocation, missing output and forged
reviewer-writable receipt/rewards remain errors. Native reward cleanup removes
stale rewards; retained proof and scorer diagnostics permit inspection.
Complete empty output remains valid. These are focused deterministic integration
checks; no live account failure or forced OAuth rotation is induced.

Python and Node judge test results for the final revision are recorded on the
pull request. The pinned Harbor's focused plan/config/retry/verifier/separate-verifier checks:
**256 passed**, with 34 upstream mock/deprecation/cache warnings.
The actual UvScript subprocess passes from an unrelated working directory using
its pinned dependency. Unequal denominators pool to precision .2, recall .3 and
balanced .24; malformed counts fail, null rewards are skipped, complete empty
rewards contribute their references. Native completion/errors are reported
separately because the UvScript interface cannot see lifecycle exceptions and
can receive a reward from a trial with a later cleanup error.
Command-scoped fixture signing was disabled only for temporary test commits;
repository hooks and Git configuration were preserved. No configured CI,
lint/typecheck command or full baseline inference was part of this delivery.

## Retained evidence

The job directories are native Harbor evidence, with job/trial config, locks,
results, host-owned receipts, trajectories, joined Daydream evidence, finalized
findings, scorer diagnostics and full judge request/response records. Use the
native viewer via `.cache/harbor-6099b8c-env/bin/harbor view jobs`. Its job table,
trial trajectory and artifact tree were exercised directly; scores and raw count
dimensions are available through the native viewer/API.

Local supporting audit files under `.cache/issue4/` include
`source-manifest-before-pilot.json`, `pilot-context-validation.json`,
`pilot-print-config.json`, `pilot-dry-run.log`, `pilot-lint.json`,
`pilot-inspection-notes.md`, `pilot-reconciliation.json`,
`attempt-reconciliation.json`, `concurrent-reconciliation.json`,
`concurrent-plan-validation.json`, `concurrent-reviewer-mounts.json`,
`concurrent-judge-mounts.json`, `viewer-pilot-job.json`,
`credential-observation-before.json`, `credential-observation-after-attempts.json`,
`credential-observation-before-concurrent.json`,
`credential-observation-after-concurrent.json`,
`credential-observation-before-refresh.json`,
`credential-observation-after-refresh.json`, `refresh-print-config.json`,
`refresh-dry-run.log`, `refresh-live.log`, `refresh-reconciliation.json`,
`refresh-reviewer-mounts.json`, `refresh-verifier-mounts.json`,
`python-tests-final.log`, `harbor-checks.log`, and `test-audit.md`.
Catalog accounting is `artifacts/aacr/issue4-tasks/catalog.json`, with separate
`full-catalog-validation.json`/`.md` and `full-catalog-lint.json` audits.
These files retain observations without introducing another execution system.

Earlier interrupted catalogs `issue4-tasks-pre-mergebase-fix` and
`issue4-tasks-pre-lfs-runtime-fix`, the unavailable-Node pilot preparation,
their logs and historical jobs remain intact. Fresh named selected jobs preserve
earlier attempts; automatic retries, error-filtered resume and reopening incomplete
jobs are avoided because they can delete intermediate evidence.

## Review

Independent read-only review against the exact baseline found no documented
standards violations. Spec review found one LFS runtime defect; its image filter
fix and regression passed, and the independent reviewer confirmed resolution.
The optional repeated Git-fixture setup was left explicit because the fixture
histories protect different regressions. Independent Standards follow-up found
no documented-standard violations in the final tracked diff and six untracked
files. Spec follow-up accepted all ten serial trial records, including successful
Linera admission and the five complete empty outputs. Final Spec follow-up found
**zero unresolved findings** in the completed concurrent/full-catalog evidence
and documentation. The agent-stage overlap is reported from Harbor's exact
intervals, which include its thin reviewer wrapper.

Code review of the full branch diff led to small corrections after every live
job in this report: conversion now sets LFS filters per context and requires the
wheel outside the Oracle fixture, and the adapter renders `--capture-data` through
Harbor's `Cli` annotation. Deterministic tests cover these changes, and the
one-case catalog `artifacts/aacr/issue4-review-fix-check` retains a real conversion
with the local filters. No live job was rerun afterward.

## Native JSONL capture follow-up

After the pilot, the user requested local `--capture-data` for inspecting and
learning from benchmark runs. Fresh Daydream configurations now enable the native
capture flag and retain its per-trial store at `agent/dataset/`; historical pilot
jobs and task assets are preserved. Older baked entrypoints require either fresh
generation or native `--agent-kwarg capture_data=false`.

The one-case validation job `jobs/aacr-issue4-capture-20261030-a`, trial
`cline-cline-2347__JRhL8Ga`, completed and scored **1/1, zero errors or retries**.
Its freshly generated task retained the exact previous Cline comparison,
reference annotations and wheel pin. The emitted native record is:

`agent/dataset/runs/fbc7cbd5cd8b4891b10f864d4189958fe3d5dbb3103aa3161d325cc7f2c706f7.jsonl`

The 526,797-byte `daydream.run.v1` JSONL validates through native
`LocalRecordStore`. It contains **five joined trajectory documents / 34 steps**,
six raw claims across two review scopes, five finalized findings, adjudication,
terminal coverage, input diff and provenance. Run identity, original diff/hash,
analyzed/final revision, native coverage artifact and final findings reconcile
with the retained execution evidence. Acquisition and trajectory status are
complete. The Hub destination is absent; no upload is configured.

Four separate Pi judge calls completed successfully afterward. This operational
check scored nine references, five findings, one matched finding and two covered
references (precision .2, recall .222, balanced .21); it is excluded from the
original ten-case aggregate. All five finalized findings and full judge responses
were inspected. Reviewer execution was 237.839 seconds; usage was 885,220 input
tokens (684,928 cached), 15,075 output, API-equivalent estimate $0.6198268;
subscription charges remain unknown. AACR judging remains in `verifier/` and is
separate from the record's native intrinsic scoring.

TDD extended existing adapter and composed-plan keeper tests: six expected
regressions were observed before implementation; focused checks then passed.
The repository suite then passed **164 tests**. Two pinned native capture tests
passed using the existing Daydream test runtime, proving JSONL emission without
a Hub destination and explicit capture disablement. The initial attempt with the
benchmark's pytest environment could not collect those upstream tests because its
xdist plugin was absent; the existing native environment supplied the required
plugins without changing dependencies. Native print-config/dry-run validated the
single selected case, capture flag, protected verifier and zero retries; the
legacy capture-disable override was also verified. Independent read-only review
found no issues in the capture integration. Detailed evidence is retained under
`.cache/issue4/capture-data/`, including `record-audit.json` and test logs.
