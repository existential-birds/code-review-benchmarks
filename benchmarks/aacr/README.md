# AACR

One pinned review case, Laravel framework PR #56591, supports an Oracle
verification fixture ([#2](https://github.com/existential-birds/code-review-benchmarks/issues/2))
and a complete Daydream review ([#3](https://github.com/existential-birds/code-review-benchmarks/issues/3)).
The parent issue's pilot and full baseline remain separate work.

- Use `.cache/harbor-6099b8c-env/bin/harbor`, backed by checkout revision
  `6099b8cf2bfbbb3e7ff1eb2163bfc4524dbd9ba5` (Harbor 0.24.0). PATH Harbor and
  `/Users/ka/github/harbor/.venv` have different versions. Preserve that checkout's
  existing environment and editor changes.
- Set `AACR_PI_AUTH_DIR` to the canonical native Pi judge directory. If needed,
  use Pi's `/login` for OpenAI Codex there. The directory must remain writable
  for Pi's credential refresh and locking.
- Oracle Harbor lint exits 1 with `compose-external-bind-mount` and
  `compose-writable-bind-mount`: the credential bind is intentional. Retain the
  report and resolve any additional findings.
- Treat Pi's SDK cost figures as API-equivalent estimates, not subscription
  charges.

Accepted Oracle run: `jobs/aacr-oracle-final`, trial
`laravel-framework-56591__UypUvTg`: 1/1 completed, zero errors; precision, recall
and balanced score all 1.0. `jobs/aacr-oracle` retains the earlier temperature
failure; use a fresh named job when rerunning.

## Daydream

The wheel pin is in [daydream-build.json](daydream-build.json): signed immutable
source `5bae1ac09918ecf8d095aaf6d283235ca1ad83a8`, including the credential-lock
removal for [#9](https://github.com/existential-birds/code-review-benchmarks/issues/9).
The resulting `daydream-0.30.0-py3-none-any.whl` has SHA-256
`923075dff2be5e6d5b2bdc670eb5e247fc86efc391ae5d6c23eee35e4ca63d92`.
Build from a fresh isolated archive; preserve the earlier `.cache/daydream-wheel`:

```sh
mkdir -p .cache
mkdir .cache/daydream-5bae1ac0-issue9-source
git -C /path/to/daydream-issue-9-codex-lock archive 5bae1ac09918ecf8d095aaf6d283235ca1ad83a8 | \
  tar -x -C .cache/daydream-5bae1ac0-issue9-source
(cd .cache/daydream-5bae1ac0-issue9-source && uv build --wheel --out-dir ../daydream-5bae1ac0-issue9-wheel)
```

Conversion and adapter tests need Python 3.14, that wheel, and the pinned Harbor
checkout installed. Use Daydream's hash-locked
`daydream/benchmark/harbor/runtime-requirements.lock` for core dependencies.
Native `reviewer_build()` supplies Python 3.14.3, Codex CLI 0.163.0, and the
frozen wheel/runtime assets for the sandbox. Task metadata and Harbor locks
retain source, wheel/dependency hashes, and runtime identities.
The resulting sandbox runtime-lock SHA-256 is
`a8718d3998f60bb41b32e249a9d24722c1d493b399c9db1098feca8d6e5c5100`;
the host dependency-lock SHA-256 is
`12925ef3adb5965ad1e7b8c3fcaf77b998dc0216075ab5f68840052012b665db`.
AACR retains its Harbor source pin `6099b8cf2bfbbb3e7ff1eb2163bfc4524dbd9ba5`.
Install this exact wheel into the Python 3.14 converter/test environment before
generating any tasks:

```sh
uv pip install --python .venv/bin/python --require-hashes \
  -r .cache/daydream-5bae1ac0-issue9-source/daydream/benchmark/harbor/runtime-requirements.lock
test "$(git -C /path/to/pinned/harbor rev-parse HEAD)" = \
  6099b8cf2bfbbb3e7ff1eb2163bfc4524dbd9ba5 && \
  uv pip install --python .venv/bin/python -e /path/to/pinned/harbor
uv pip install --python .venv/bin/python --no-deps --reinstall \
  .cache/daydream-5bae1ac0-issue9-wheel/daydream-0.30.0-py3-none-any.whl
```

Install the pinned Harbor checkout with dependencies after the core lock: host
Harbor requirements can need newer dependencies, such as `certifi`, than the
isolated sandbox's core lock. The generated sandbox runtime remains frozen.

Verify the imported package and packaged assets match that wheel,
then check the fresh task's `environment/aacr/build.json`, `tests/build.json`,
and task metadata against the pin. `--daydream-wheel` alone is insufficient:
conversion imports the host-installed `reviewer_build()` and emits that
package's runtime, profile, and authentication assets. Preserve historical task
catalogs, caches, and job evidence by choosing a fresh output directory.

Set `AACR_CODEX_AUTH_DIR` to a dedicated native Codex subscription directory;
keep `AACR_PI_AUTH_DIR` separate, with an independent Pi subscription login.
Only the reviewer mounts Codex at `/run/aacr-codex`; only the separate verifier
mounts Pi at `/run/aacr-pi`. Both stores stay outside task assets and results.
Reuse the accepted stores without repeated login. Concurrent reviewer trials
share the canonical Codex credentials through native symlinks in private role
homes; Daydream imposes no whole-review credential lock. Pi retains its native
refresh locking. An existing inert regular `.reviewer.lock` is admissible and
needs no deletion or migration. Validate the stores before launching:

```sh
.venv/bin/python - <<'PY'
import os
from pathlib import Path
from daydream.benchmark.reviewbench.harbor_auth import validate_credential_stores
validate_credential_stores(Path(os.environ['AACR_CODEX_AUTH_DIR']),
                          Path(os.environ['AACR_PI_AUTH_DIR']))
PY

.venv/bin/python -m benchmarks.aacr.convert \
  --upstream /path/to/clean/pinned/aacr-bench \
  --output <fresh-task-catalog> \
  --daydream-wheel .cache/daydream-5bae1ac0-issue9-wheel/daydream-0.30.0-py3-none-any.whl
.cache/harbor-6099b8c-env/bin/harbor lint <fresh-task-catalog>/laravel-framework-56591 --json
.cache/harbor-6099b8c-env/bin/harbor run -c experiments/aacr-daydream.yaml \
  -p <fresh-task-catalog> -i 'laravel-framework-56591' \
  -k 1 -n 1 -r 0 --job-name <fresh-name> --print-config
.cache/harbor-6099b8c-env/bin/harbor run -c experiments/aacr-daydream.yaml \
  -p <fresh-task-catalog> -i 'laravel-framework-56591' \
  -k 1 -n 1 -r 0 --job-name <fresh-name> --dry-run
.cache/harbor-6099b8c-env/bin/harbor run -c experiments/aacr-daydream.yaml \
  -p <fresh-task-catalog> -i '<exact-review-case-name>' \
  -k <attempt-count> -n <concurrency> -r 0 --job-name <fresh-name>
```

Both experiment YAMLs explicitly set `retry.max_retries: 0`; retain that setting
and `-r 0`. Attempt count (`-k`) creates independent trials for each selected
review case and reviewer; it is separate from automatic retries. Set concurrency
with native `-n` as needed. Pair CLI selection (`-i`) with the catalog (`-p`):
`-i` alone does not filter a dataset inherited only from configuration. For the
Oracle fixture use `experiments/aacr-oracle.yaml` and its fresh task catalog.
Use `--print-config` and `--dry-run` to inspect the selected plan before execution.
Printed JSON omits default values; rehydrate it with the pinned Harbor's
`harbor.models.job.config.JobConfig` to check that `retry.max_retries == 0` and
that attempt count, concurrency, and verifier settings resolve as intended.

Use a fresh, uniquely named native job for every selected rerun and preserve the
earlier job directories. Harbor retains ordinary and final errors with the
available recovered outputs, native results, and exception diagnostics. Before
another automatic retry, Harbor deletes that intermediate failed trial's
directory; zero retries avoids this deletion. `harbor jobs resume` with an error
filter deletes matching failed trial directories (its default filter includes
`CancelledError`). Reopening an existing job can also delete incomplete trial
directories that lack `result.json`. Neither operation preserves all earlier
evidence, so use fresh named jobs for this workflow. No suite retry runner or
archive subsystem is needed.

The experiment uses native `auto_mounting: true` for agent logs. Process
completion is recorded separately in the trial-root `daydream-receipt.json`,
outside reviewer-writable mounts. The configured `DaydreamVerifier` uploads
that host-owned document directly to the separate verifier before delegating
to Harbor's default verifier. Keep `verifier.import_path` from the experiment
configuration when overriding other job settings. Regenerate Daydream tasks
created before this receipt boundary changed: their verifier scripts still read
the old reviewer-writable path. Use a fresh output directory with the conversion
command above; the converter intentionally refuses to replace existing tasks.
The reviewer Compose service permits nested
read-only Codex sandbox namespaces with `seccomp=unconfined`; installation checks
namespace support before inference. Its native task settings provide public
networking, four CPUs, 8 GiB memory, and a 5,400-second review deadline. Lint's
credential-bind findings are intentional. Investigate additional findings.

Harbor-first inspection at `6099b8c` covered `agents/installed/base.py`,
`agents/factory.py`, `agents/installed/codex.py`, `trial/trial.py`,
`trial/artifact_handler.py`, the custom-agent/job docs, and separate-verifier
tests. Receipt hardening additionally inspected `verifier/base.py`,
`verifier/factory.py`, `verifier/verifier.py`, `models/trial/paths.py`,
`tests/unit/test_verifier_factory.py`, `tests/unit/test_trial_verifier_separate.py`,
and the custom-verifier docs. Harbor already owns execution controls, artifact
transfer, and the separate verifier; its Codex agent invokes Codex directly.
The custom installed agent invokes Daydream once and exports finalized findings;
the small native verifier subclass supplies the missing host-owned receipt
boundary. It rejects missing/unsuccessful receipts and reviewer-shared environments,
then retains native test upload, execution, reward cleanup, and result parsing.
The custom-verifier factory reuploads the trusted baked tests from their source.
Zero-retry workflow inspection additionally covered `models/job/config.py`,
`job_plan.py::JobPlan.build_trial_configs`,
`trial/queue.py::TrialQueue._execute_trial_with_retries`, `trial/trial.py`,
`cli/jobs.py::resume`, `job.py::Job._maybe_init_existing_job`,
`docs-mintlify/jobs/configs.mdx`, and the native retry, queue, job-plan, and config
persistence tests. Harbor provides selection, attempts, configurable concurrency,
retries, error retention, storage, and aggregation; there is no orchestration gap
requiring custom code or a change to ADR 0001.

Daydream uses packaged default review settings, precision mode off, empty file
configuration, and isolated execution inputs. It applies no fixes, publication,
or training capture. The separate verifier requires successful matching process
receipts, native complete terminal coverage, unchanged base/head/full-patch
identity, and finalized findings hashes before scoring. Incomplete or stale
output cannot publish rewards; complete empty reviews remain valid. Unknown-line
findings and AACR's existing matching/count semantics are preserved.

Inspect job/trial `result.json` and `lock.json`, `agent/trajectory.json`, joined
native evidence under `agent/daydream/`, findings and completion proof under
`artifacts/logs/artifacts/`, the trial-root `daydream-receipt.json`, and
`verifier/` scores and judge evidence. Native
callback usage and top-level token totals include sibling reviews; Harbor's
backfilled per-model map covers the root trajectory only. Monetary estimates are
API-equivalent, not subscription charges.

Accepted Daydream run at benchmark revision `810b5f5b1e6f55157dcc0692149861588be700c7`:
`jobs/aacr-daydream-issue3-final-810b5f5`, trial
`laravel-framework-56591__eKQiJJw`: **1/1 completed, zero errors**, with complete
analysis and successful separate scoring through the protected host-to-verifier
receipt boundary. The task was freshly generated from the pinned AACR source;
its reviewer/scorer assets match that revision. It retained zero findings against
one reference, so both match counts and all three agreement scores are **0.0**;
no judge completion calls were needed. Reviewer usage was 328,222 input tokens
(260,352 cached) and 1,890 output tokens over 43.5 seconds; total Harbor job time
was 2 minutes 41 seconds. The Oracle run remains the live subscription-judge
inference evidence. Agreement scores do not establish actual bug precision.
These historical jobs used the earlier wheel. The lock-removal regression
uses deterministic child processes and synthetic credential rotation; it does
not prove live OAuth refresh or a new live reviewer/judge run.

Run deterministic checks with `.venv/bin/python -m pytest tests` and
`node --test tests/pi_judge.test.mjs`. Daydream adapter and completion tests need
the pinned native runtime; inference doubles are confined to tests.

## Upstream

The vendored evaluator comes from [Alibaba AACR-Bench](https://github.com/alibaba/aacr-bench)
and shares the root [Apache-2.0 license](../../LICENSE). Generated Laravel
repository context retains its own license.

The vendored converter has one local correction: an existing raw-data file whose
SHA-256 differs from its metadata is rejected when no download URL is available.
The pinned task generator retains its independent dataset checksum check.
