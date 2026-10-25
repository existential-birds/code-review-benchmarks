#!/bin/sh
set -eu
mkdir -p /logs/artifacts
cp /solution/findings.json /logs/artifacts/findings.json.tmp
mv /logs/artifacts/findings.json.tmp /logs/artifacts/findings.json
