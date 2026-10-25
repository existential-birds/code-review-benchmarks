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

The wheel pin is in [daydream-build.json](daydream-build.json): source
`05f067e96e51c63ff4bad966100bb638bf906346`, from incoming
[Daydream PR #1688](https://github.com/existential-birds/daydream/pull/1688).
Build from a fresh isolated archive:

```sh
mkdir -p .cache
mkdir .cache/daydream-05f067e-source
git -C /path/to/daydream archive 05f067e96e51c63ff4bad966100bb638bf906346 | \
  tar -x -C .cache/daydream-05f067e-source
(cd .cache/daydream-05f067e-source && uv build --wheel --out-dir ../daydream-wheel)
```

Conversion and adapter tests need Python 3.14, that wheel, and the pinned Harbor
checkout installed. Use Daydream's hash-locked
`daydream/benchmark/harbor/runtime-requirements.lock` for core dependencies.
Native `reviewer_build()` supplies Python 3.14.3, Codex CLI 0.163.0, and the
frozen wheel/runtime assets for the sandbox. Task metadata and Harbor locks
retain source, wheel/dependency hashes, and runtime identities.

Set `AACR_CODEX_AUTH_DIR` to a dedicated native Codex subscription directory;
keep `AACR_PI_AUTH_DIR` separate, with an independent Pi subscription login.
Only the reviewer mounts Codex at `/run/aacr-codex`; only the separate verifier
mounts Pi at `/run/aacr-pi`. Both stores stay outside task assets and results.
Validate them before launching:

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
  --output artifacts/aacr/daydream-tasks \
  --daydream-wheel .cache/daydream-wheel/daydream-0.30.0-py3-none-any.whl
.cache/harbor-6099b8c-env/bin/harbor lint artifacts/aacr/daydream-tasks/laravel-framework-56591 --json
.cache/harbor-6099b8c-env/bin/harbor run -c experiments/aacr-daydream.yaml --print-config
.cache/harbor-6099b8c-env/bin/harbor run -c experiments/aacr-daydream.yaml --dry-run
.cache/harbor-6099b8c-env/bin/harbor run -c experiments/aacr-daydream.yaml \
  -k 1 -n 1 -r 0 --job-name <fresh-name>
```

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
