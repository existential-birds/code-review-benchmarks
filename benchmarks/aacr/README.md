# AACR agent notes

This is [issue #2](https://github.com/existential-birds/code-review-benchmarks/issues/2)'s
Oracle verification fixture. Acceptance requires one live trial; it does not
establish a Daydream baseline or upstream model-score parity.

- Use `.cache/harbor-6099b8c-env/bin/harbor`, backed by checkout revision
  `6099b8cf2bfbbb3e7ff1eb2163bfc4524dbd9ba5` (Harbor 0.24.0). PATH Harbor and
  `/Users/ka/github/harbor/.venv` have different versions. Preserve that checkout's
  existing environment and editor changes.
- Set `AACR_PI_AUTH_DIR` to the canonical native Pi judge directory. If needed,
  use Pi's `/login` for OpenAI Codex there. The directory must remain writable
  for Pi's credential refresh and locking.
- Default Harbor lint exits 1 with `compose-external-bind-mount` and
  `compose-writable-bind-mount`: the credential bind is intentional. Retain the
  report and resolve any additional findings.
- Treat Pi's SDK cost figures as API-equivalent estimates, not subscription
  charges.

Accepted live run: `jobs/aacr-oracle-final`, trial
`laravel-framework-56591__UypUvTg`: 1/1 completed, zero errors; precision, recall
and balanced score all 1.0. `jobs/aacr-oracle` retains the earlier temperature
failure; use a fresh named job when rerunning.

The vendored evaluator comes from [Alibaba AACR-Bench](https://github.com/alibaba/aacr-bench)
and shares the root [Apache-2.0 license](../../LICENSE). Generated Laravel
repository context retains its own license.

The vendored converter has one local correction: an existing raw-data file whose
SHA-256 differs from its metadata is rejected when no download URL is available.
The pinned task generator retains its independent dataset checksum check.
