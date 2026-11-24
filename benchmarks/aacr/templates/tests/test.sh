#!/bin/sh
set -eu
cd /tests/suite
exec python -m benchmarks.aacr.score \
  --instance /tests/instance.jsonl \
  --findings /logs/artifacts/findings.json \
  --judge /tests/judge.json \
  --output /logs/verifier
