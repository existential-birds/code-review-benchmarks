# AACR Oracle verification fixture

## Harbor capability inspection

Inspected Harbor at `6099b8cf2bfbbb3e7ff1eb2163bfc4524dbd9ba5` before
implementation. Its pre-existing `.vscode/settings.json` edit is unrelated.

- `src/harbor/agents/oracle.py::OracleAgent.run` uploads `solution/` and
  executes `solve.sh`; a nonzero exit is recorded rather than raised.
- `docs-mintlify/tasks/separate-verifier.mdx`,
  `examples/tasks/separate-verifier-environment/`, and
  `src/harbor/trial/trial.py::_run_separate_verifier` provide stock isolated
  verification and native `/logs/artifacts` transfer. Dedicated verifier
  builds must bundle `/tests/test.sh` and assets. Agent mounts/compose overlays
  are cleared for the verifier, so its credential bind belongs under `tests/`.
- `tests/unit/test_trial_verifier_artifact_transfer.py` and
  `tests/unit/test_trial_verifier_separate.py` cover artifact restoration,
  sanitization, environment selection and isolation.
- `src/harbor/verifier/verifier.py::Verifier.verify` collects diagnostics and
  parses numeric rewards; test exit status alone is not a grading guard.
  Harbor removes stale reward files. This integration must validate completed
  findings and sticky judge failures before publishing any reward.
- `src/harbor/models/job/config.py::JobConfig`,
  `DatasetConfig._filter_task_ids`, `src/harbor/job_plan.py::JobPlan.build_trial_configs`
  and `src/harbor/trial/queue.py::TrialQueue` already own selection, attempt
  count (k), concurrency, retries, timeouts, scheduling and result storage.
- `docs-mintlify/results/view-job-results.mdx` describes terminal results and
  native `harbor view` inspection. No custom aggregation is needed for one case.

Searches of Harbor `src/`, `adapters/`, `tests/`, `docs-mintlify/` and
`registry.json` found no AACR integration. Harbor's installed Pi/Codex reviewer
agents do not provide AACR's asynchronous Chat Completions scorer interface.
The only custom gaps are mapping a pinned AACR review case to ordinary task
assets and bridging that scorer interface to Pi's subscription SDK. Execution,
artifact transfer and grading remain native Harbor behavior.

## Inputs and isolation

This is issue [#2](https://github.com/existential-birds/code-review-benchmarks/issues/2)'s
verification fixture. Oracle is the reviewer. It is not a Daydream reviewer,
pilot, production reviewer or baseline. Only one live trial with attempt count
(k) = 1 is required. A pooled metric bridge, other judges/benchmarks and CI are
outside this fixture.

AACR is pinned to `ee9d7fef70ef31acea2ff7ae316ce0a9575fe4df`.
The default evaluator input `dataset/positive_samples.json` has 196 review cases
and 1,506 reference findings; SHA-256
`02c8561f1c3994a26f427d479b2a3f6e6c8e6858161fbc903b45248fa2cb2097`.
The negative-comments dataset is not a reference set here.

The selected review case is [Laravel PR #56591](https://github.com/laravel/framework/pull/56591):

| Metadata | Value |
| --- | --- |
| Upstream identity | `laravel__framework@0a5238a` |
| Repository / language | `laravel/framework` / PHP |
| Base | `6d94b16fd5f4f72bb741f9b900b83179c8ab46c9` |
| Head | `0a5238acfc4ca99c363283bf3bb16e20905f2a39` |
| Change | One commit, one file, four additions |
| Reference findings | One, in `src/Illuminate/Database/Console/Migrations/MigrateMakeCommand.php` |

`convert.py` checks the upstream revision, clean source checkout and positive
input hash. It calls upstream `convert_record`, round-trips through
`schema.load_instances`, then calls `repo_utils.prepare_repo` only inside a fresh
generated directory. Both full revisions must exist, head must match and the
worktree must be clean. An existing generated task is never overwritten.

The generated agent image contains only `environment/repo`, including Git
history for comparison. Its build context cannot access `tests/`. Instructions
contain revision context and the finding contract. Raw annotations are written
only to `tests/instance.jsonl` in the separate verifier build context. The
ordinary `solution/solve.sh` publishes a fixed, independently worded Oracle
finding, preserving file context with null line anchors, atomically to
`/logs/artifacts/findings.json`. Harbor transfers it to the verifier.

The imported evaluation files are unchanged, with upstream license and hashes
in `upstream/LICENSE` and `upstream/provenance.json`; see root `NOTICE`.
All new project code is Apache-2.0. Generated Laravel context retains its own
license. Credentials and generated assets/results are not committed.

## Scoring and failure contract

`score.py` requires a JSON object with a `review_output` array. Each entry needs
file context, nonempty summary/description text, and positive or null line
anchors. Missing, unreadable or invalid findings fail. `review_output: []` is a
completed empty review and scores zero with the reference denominator retained.

The wrapper uses upstream `evaluate.evaluate` with its Codex finding builder
and loader. This is a supported finding format; the reviewer is still Oracle.
It preserves `build_reference_comments`, `evaluate_file_level_semantics`,
`match_llm_file_level`, `compute_cr_statistics`, `_compute_summary` and
`_compute_balanced_score`, including:

- Matching only within the same normalized file and the final recognized
  bracketed integer-array verdict, with valid `[]` meaning a nonmatch.
- Generated/reference denominators, duplicate findings counting toward generated
  findings, and only findings adding uncovered references contributing to the
  distinct matched-generated count. One finding can cover several references.
- Three-decimal precision and recall before balanced-score calculation.
- Upstream per-finding rationales, including its 500-character truncation, plus
  separate full prompt/response evidence.

The adapter is explicitly installed with `judge.USE_MOCK_LLM = False` and the
real client; it never supplies a dummy API key. `PiJudgeClient` implements only
async `chat.completions.create` and `choices[0].message.content`. Authentication,
subprocess, transport, SDK error/aborted results and absent/nontext output leave
sticky failure evidence. A narrow guard rejects missing/unparseable verdict
arrays using AACR's own recognized-array rule, without changing its matching
parser or index filtering. After upstream catches errors, the wrapper checks
both failure ledgers and requires exactly the intended review case evaluated
once with zero missing cases. Only then does it atomically publish reward.json.
There is no reward.txt or successful reward on failure in a stock Harbor trial.

`verifier/score.json` and `verifier/upstream/metrics_*.json` retain full upstream
results. `verifier/reward.json` exposes precision (`file_semantic_match_rate`),
recall (`file_semantic_recall_rate`), `balanced_score`, `expected_notes`,
`generated_notes`, `matched_generated_notes` and `matched_file_semantic_notes`.
`verifier/verdicts.json` retains full texts; `verifier/judge/judge-call-*.json`
retains prompts, full responses, transport identity/settings, usage and timing.
`verifier/diagnostic.json` distinguishes failed verification from scored
nonmatches. Credentials, SDK error bodies, headers and stderr are not retained.

## Subscription transport

`package-lock.json` pins `@earendil-works/pi-coding-agent` and `pi-ai` to 1.4.0.
The helper checks actual installed versions and uses public `ModelRuntime`:
`authPath=/run/aacr-pi/auth.json`, `modelsPath=null`, `allowModelNetwork=false`.
It selects the built-in `openai-codex/gpt-6.1-sol`, requires native OAuth, and uses
Pi's completion and credential refresh/locking. No internal SDK auth imports,
custom OAuth, proxy, credential copying, mock or API-key fallback is added.

Inspected Pi `dist/core/auth-storage.js`, `dist/core/model-runtime.js/.d.ts`,
`pi-ai/dist/auth/resolve.js`, and the Codex provider/transport. The credential
mount borrows only the generic directory-bind structure from Weebill
`6bafed666bbee44974443a4f4b61f5a7df0ce4bd:experiments/lab.py::_COMPOSE`, its
running-experiments documentation and tests. Daydream parent
`05f067e96e51c63ff4bad966100bb638bf906346`'s `harbor_profile.py::_compose`,
`harbor_auth.py`, `judge_auth.mjs` and auth tests were reference material;
its custom auth/symlink behavior is not copied. Reference repositories are
not modified.

Mount the canonical native Pi judge directory writable so Pi can create
`auth.json.lock` and refresh its own credentials. Only the verifier's
`tests/docker-compose.yaml` has this bind, with `create_host_path: false`.
Set `AACR_PI_AUTH_DIR` to that external directory. If authentication is needed,
use Pi's native `/login` for OpenAI Codex in that directory, then rerun as a
fresh named Harbor job after preserving failed evidence. Do not substitute API
keys or a mock judge. Node must be >=22.19.0; the verifier image pins Node 24.18.0
and both Docker base image digests.

Requested AACR options remain in evidence. Effective Codex transport uses SSE,
zero SDK retries and provider-default reasoning. The subscription endpoint rejects
`temperature`, so the adapter omits it while retaining AACR's requested value.
Pi 1.4 does not transmit upstream `top_p`, `chat_template_kwargs` or `max_tokens`
as a server output limit. No option equivalence or upstream model-score parity is claimed.
Usage is judge usage, separate from Oracle; any SDK cost figures are API-equivalent
estimates, not actual subscription charges.

## Setup and native Harbor commands

Run from the suite root. The PATH Harbor (0.22.0) and the reference checkout's
existing `.venv` metadata (0.3.0) are not the pinned runtime. Preserve that
`.venv` and its pre-existing editor changes. Use supported uv 0.13.5 to create an
isolated editable Harbor 0.24.0 environment from the exact checkout:

```sh
uv tool run --from uv==0.13.5 uv --version
UV_PROJECT_ENVIRONMENT="$PWD/.cache/harbor-6099b8c-env" \
  uv tool run --from uv==0.13.5 uv sync \
  --project /Users/ka/github/harbor --locked --no-dev
git -C /Users/ka/github/harbor rev-parse HEAD
.cache/harbor-6099b8c-env/bin/python -c \
  'import harbor, importlib.metadata; print(harbor.__file__); print(importlib.metadata.version("harbor"))'

uv venv .venv
uv pip install -e '.[test]'
npm ci --prefix benchmarks/aacr
.venv/bin/python -m pytest -q
node --test tests/pi_judge.test.mjs
```

The source revision must print `6099b8cf2bfbbb3e7ff1eb2163bfc4524dbd9ba5`,
import path `/Users/ka/github/harbor/src/harbor/__init__.py`, version 0.24.0.
The existing `.cache` environment is intentionally separate from Harbor's `.venv`.

Use the clean existing AACR checkout or reacquire it at the pin. Generation
needs Git/network access and creates an isolated repository; it does not call a
judge or execute a Harbor trial:

```sh
# If the prepared temporary checkout no longer exists:
git clone https://github.com/alibaba/aacr-bench.git .cache/aacr-upstream
git -C .cache/aacr-upstream checkout ee9d7fef70ef31acea2ff7ae316ce0a9575fe4df
.venv/bin/python -m benchmarks.aacr.convert \
  --upstream .cache/aacr-upstream --output artifacts/aacr/tasks

export AACR_PI_AUTH_DIR=/absolute/path/to/native/pi-judge-directory
.cache/harbor-6099b8c-env/bin/harbor run -c experiments/aacr-oracle.yaml --print-config
.cache/harbor-6099b8c-env/bin/harbor run -c experiments/aacr-oracle.yaml --dry-run
.cache/harbor-6099b8c-env/bin/harbor lint --json \
  artifacts/aacr/tasks/laravel-framework-56591
.cache/harbor-6099b8c-env/bin/harbor run -c experiments/aacr-oracle.yaml \
  -k 1 -n 1 -r 0 --job-name aacr-oracle
.cache/harbor-6099b8c-env/bin/harbor view jobs
```

Native job YAML selects one task, Oracle, k=1, concurrency=1 and retries=0.
Harbor owns scheduling, all timeouts, storage and mean aggregation. There is no
suite runner, custom verifier, second execution schema or aggregate metric.

Default lint intentionally reports `compose-external-bind-mount` and
`compose-writable-bind-mount` for the native credential directory. Harbor has no
per-mount allowlist. The writable external bind is needed for native refresh
and locking; do not suppress the report or call default lint successful. Retain
the entire JSON report and resolve any other findings.

Inspect `jobs/<job>/config.json`, `lock.json`, `result.json`, each trial's
`config.json`, `lock.json`, `result.json`, `agent/`, `verifier/` and `artifacts/`
through native files or `harbor view`. Findings are under
`artifacts/logs/artifacts/findings.json`. Job/trial errors remain separate from
numeric reference-agreement scores.

## Deterministic proof

The focused Python tests run the actual upstream converter/schema, isolated Git
preparation, finding builder, file-semantic scorer and evaluator/summary. Only
judge transport is replaced in scoring tests. They cover duplicates, one-to-many
coverage, normalized files, nullable anchors, empty findings, legitimate `[]`,
malformed verdicts, upstream-swallowed failures, missing/invalid findings,
sticky transport failure and full rationale retention. Transport tests separately
exercise process decoding/evidence and the public Pi runtime contract. Test
doubles live only under `tests/`; these results do not establish live model
agreement. Live evidence is recorded separately below.

## Live acceptance evidence (2026-10-24)

Final reviewed-code run: `jobs/aacr-oracle-final`, trial
`laravel-framework-56591__UypUvTg`, task checksum
`84c30e357c854357042c1e92978601d18337ff717713bd10ad25047c3e953d82`.
It used the YAML above with `--job-name aacr-oracle-final -k 1 -n 1 -r 0`.
Native job/trial configs and locks confirm one selected review case, one Oracle
trial, attempt count (k) 1, concurrency 1, retries 0 and pinned Harbor 0.24.0 at
`6099b8cf2bfbbb3e7ff1eb2163bfc4524dbd9ba5`. Completion was 1/1 with zero errors;
job wall time was about 39 seconds. The task's bundled scorer/transport/Node
helper/lock bytes match the delivered runtime source.

| Retained metric | Value |
| --- | ---: |
| `expected_notes` | 1 |
| `generated_notes` | 1 |
| `matched_file_semantic_notes` (distinct covered references) | 1 |
| `matched_generated_notes` (findings adding uncovered references) | 1 |
| `file_semantic_match_rate` | 1.0 |
| `file_semantic_recall_rate` | 1.0 |
| `balanced_score` | 1.0 |

The retained full judge text ends in `[1]`; the upstream finding record has
`matched_gt_indices: [0]`, `hit_new_gt: true` and null generated line anchors.
Raw transferred findings equal the verifier's finding input; upstream score
summary, reward.json, trial rewards and native job metrics reconcile exactly.
There are no missing cases or agent exit-code errors. Both revisions remain
available in the clean generated repository.

The sole judge call in this final trial records HTTP 200, OAuth subscription
`openai-codex/gpt-6.1-sol`, Pi SDK/pi-ai 1.4.0, Node 24.18.0 and stop reason
`stop`. It took 2.446493 seconds and records 308 input + 27 output = 335 judge
tokens. Oracle has no model token usage; Harbor's model-usage fields therefore
remain empty. Judge usage is separately retained under `verifier/judge/`.
SDK cost data is an API-equivalent estimate; actual subscription charges are
unknown. Native Pi handled the writable credential store successfully and left
no active auth lock. Credentials were neither copied into the task nor emitted
as evidence.

Retained development runs are distinct evidence. `jobs/aacr-oracle` reached the
subscription endpoint but failed with HTTP 400 / SDK `error`: temperature was
unsupported. Its diagnostic, upstream-swallowed nonmatch result and
`RewardFileNotFoundError` remain; it published neither reward file. One diagnostic
request confirmed only the allowlisted unsupported-parameter classification in
`.cache/aacr-subscription-diagnostic.json`, without a successful inference.
After omitting the unsupported option, `jobs/aacr-oracle-verified` passed with
one case/attempt, before the final review correction. The final run above
includes the correction and is the acceptance result.

Final checks: 32 Python tests and 6 Node tests passed without skips; full
positive-input conversion produced 196 schema-valid cases with zero skips;
all seven imported source/license files match the pinned upstream byte for
byte. Native `--print-config` and `--dry-run` succeeded (one trial).
`.cache/aacr-lint.json` contains exactly the two expected credential-bind findings
and lint exits 1; default lint did not pass. `git diff --check` passed.

Independent read-only review used exact baseline
`4d8a9d433396f1df394cafefb2838cd75f0cea83`, including all new untracked files.
The Standards axis found one P2: a private 600-second transport deadline.
It was removed so Harbor owns timeout policy; a simulated 601-second completion
failed before the correction and passed afterward. The independent recheck
confirmed resolution. The Spec axis found zero actionable findings. No open
review findings remain.

`harbor view jobs` was built and exercised through the native browser UI:
job/trial summaries, all numeric rewards, raw Oracle artifact and full judge
request/response are inspectable. The local viewer build used Bun 1.3.12 from
an ignored tool directory; Harbor's tracked viewer lockfiles were unchanged.
No commit, push, PR, issue mutation or merge was performed.

Native validation notices are retained in command/trial logs: Harbor skips Oracle
kwargs-schema validation because Oracle exposes no options schema (no kwargs are
configured). Pre-build image OS inspection was skipped when the image was not
present; the pinned Linux images subsequently built and executed successfully.
These notices are not claimed as passed checks. No implementation test was
skipped, and no broader pilot, Daydream review or baseline was run.
