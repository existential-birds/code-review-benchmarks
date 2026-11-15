.DEFAULT_GOAL := help
.DELETE_ON_ERROR:
.PHONY: help setup quickstart view test aacr-source reviewbench-source prereqs

HARBOR_COMMIT := 6099b8cf2bfbbb3e7ff1eb2163bfc4524dbd9ba5
# The other pins live in the code that checks them.
DAYDREAM_COMMIT := $(shell sed -n 's/.*"source_commit": "\([0-9a-f]*\)".*/\1/p' benchmarks/daydream/build.json)
AACR_COMMIT := $(shell sed -n "s/^AACR_REVISION = '\([0-9a-f]*\)'/\1/p" benchmarks/aacr/convert.py)
REVIEWBENCH_COMMIT := $(shell sed -n "s/^REVIEWBENCH_REVISION = '\([0-9a-f]*\)'/\1/p" benchmarks/reviewbench/convert.py)

# Where Codex and Pi keep their logins, unless set.
REVIEWER_CODEX_AUTH_DIR ?= $(or $(CODEX_HOME),$(HOME)/.codex)
JUDGE_PI_AUTH_DIR ?= $(or $(PI_CODING_AGENT_DIR),$(HOME)/.pi/agent)
export REVIEWER_CODEX_AUTH_DIR JUDGE_PI_AUTH_DIR

CASE ?= bluewave-labs-checkmate-1897
QUICKSTART := artifacts/aacr/quickstart/$(CASE)
SRC := .cache/src
PY := .venv/bin/python
SETUP := .venv/.setup

# $(call fetch,<repository>,<commit>,<folder>): a clean checkout of one commit.
define fetch
rm -rf $(3) && git init -q $(3) && git -C $(3) fetch -q --depth 1 $(1) $(2) && git -C $(3) checkout -q FETCH_HEAD
endef

help:
	@echo 'make quickstart          Review and score one AACR case with the pinned Daydream (CASE=<name> for another)'
	@echo 'make view                Open the Harbor viewer on past jobs'
	@echo 'make setup               Build the pinned Daydream and install the suite into .venv'
	@echo 'make aacr-source         Fetch the pinned AACR-Bench into $(SRC)/aacr-bench'
	@echo 'make reviewbench-source  Fetch the pinned ReviewBench into $(SRC)/ReviewBench'
	@echo 'make test                Lint and run the tests'

quickstart: prereqs $(QUICKSTART)/catalog.json
	.venv/bin/harbor run -c experiments/aacr-baseline.yaml -p $(QUICKSTART) --job-name quickstart-$$(date +%Y%m%d-%H%M%S)

view: $(SETUP)
	.venv/bin/harbor view jobs

setup: $(SETUP)

aacr-source: $(SRC)/.aacr-bench-$(AACR_COMMIT)

reviewbench-source: $(SRC)/.ReviewBench-$(REVIEWBENCH_COMMIT)

test: $(SETUP)
	uvx ruff@0.14.0 check
	$(PY) -m pytest tests -q
	node --test tests/pi_judge.test.mjs

prereqs:
	@docker info >/dev/null 2>&1 || { echo 'Docker is not running.'; exit 1; }
	@test -f "$(REVIEWER_CODEX_AUTH_DIR)/auth.json" || { echo 'No Codex login in $(REVIEWER_CODEX_AUTH_DIR): run codex login, or set REVIEWER_CODEX_AUTH_DIR.'; exit 1; }
	@test -f "$(JUDGE_PI_AUTH_DIR)/auth.json" || { echo 'No Pi login in $(JUDGE_PI_AUTH_DIR): run /login in Pi, or set JUDGE_PI_AUTH_DIR.'; exit 1; }

$(QUICKSTART)/catalog.json: $(SETUP) $(SRC)/.aacr-bench-$(AACR_COMMIT)
	rm -rf $(QUICKSTART)
	$(PY) -m benchmarks.aacr.convert --upstream $(SRC)/aacr-bench --output $(QUICKSTART) --task $(CASE)

$(SETUP): benchmarks/daydream/build.json pyproject.toml benchmarks/pi/package-lock.json $(SRC)/.daydream-$(DAYDREAM_COMMIT)
	uv venv --clear --quiet --python 3.14 .venv
	$(PY) -m benchmarks.daydream.build $(SRC)/daydream
	uv pip install --python $(PY) --require-hashes -r .cache/daydream/runtime-requirements.lock
	uv pip install --python $(PY) "harbor @ git+https://github.com/harbor-framework/harbor@$(HARBOR_COMMIT)"
	uv pip install --python $(PY) --no-deps .cache/daydream/daydream-*.whl
	uv pip install --python $(PY) -e '.[test]'
	npm ci --omit=dev --prefix benchmarks/pi
	touch $@

$(SRC)/.daydream-$(DAYDREAM_COMMIT):
	$(call fetch,https://github.com/existential-birds/daydream,$(DAYDREAM_COMMIT),$(SRC)/daydream)
	touch $@

$(SRC)/.aacr-bench-$(AACR_COMMIT):
	$(call fetch,https://github.com/alibaba/aacr-bench,$(AACR_COMMIT),$(SRC)/aacr-bench)
	touch $@

$(SRC)/.ReviewBench-$(REVIEWBENCH_COMMIT):
	$(call fetch,https://github.com/review-bench/ReviewBench,$(REVIEWBENCH_COMMIT),$(SRC)/ReviewBench)
	npm ci --prefix $(SRC)/ReviewBench
	touch $@
