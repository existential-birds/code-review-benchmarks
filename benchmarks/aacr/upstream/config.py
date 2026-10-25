"""Centralized path and environment-variable configuration for the evaluation framework.

Keep conventions such as default directories, environment-variable names for each
stage, and naming rules in one place to avoid implicit coupling between modules.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Directory layout (resolved relative to this file for stable script locations).
EVALUATION_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = EVALUATION_DIR.parent

# Default data and output directories (all under evaluation/ for a consistent layout).
# Store each benchmark's raw files under benchmark/<benchmark_name>/; converters
# write the standard format to data/<benchmark_name>.jsonl to avoid overwrites.
BENCHMARK_DIR = EVALUATION_DIR / "benchmark"
DATA_DIR = EVALUATION_DIR / "data"
DEFAULT_REPO_DIR = EVALUATION_DIR / "repo"
DEFAULT_RESULTS_DIR = EVALUATION_DIR / "results"
DEFAULT_METRICS_DIR = EVALUATION_DIR / "metrics"


def benchmark_raw_dir(benchmark_name: str) -> Path:
    """Raw data directory for a benchmark: benchmark/<benchmark_name>/."""
    return BENCHMARK_DIR / benchmark_name


def dataset_path(benchmark_key: str) -> Path:
    """Converted standard dataset path for a benchmark: data/<benchmark_key>.jsonl.

    benchmark_key uses lowercase with underscores (e.g. aacr_bench), matching the converter module.
    """
    return DATA_DIR / f"{benchmark_key}.jsonl"


def benchmark_name_from_dataset(dataset_path_value: Path) -> str:
    """Derive the benchmark name from the dataset filename without its extension.

    Example: data/aacr_bench.jsonl -> aacr_bench, used as a results/metrics subdirectory.
    """
    return Path(dataset_path_value).stem


def reviewer_results_dir(benchmark_name: str, reviewer: str) -> Path:
    """Results root for a benchmark and reviewer: results/<benchmark>/<reviewer>/.

    Each review creates a separate run subdirectory (see make_run_id / run_dir)
    to prevent evaluations from overwriting one another.
    """
    return DEFAULT_RESULTS_DIR / benchmark_name / reviewer


def make_run_id(run_id: Optional[str] = None) -> str:
    """Determine the directory name for this run.

    Use the supplied run_id after sanitizing invalid characters, or generate a
    YYYYmmdd_HHMMSS timestamp. Users can choose names such as baseline or v2 and
    use the same name to locate the run for later evaluation.
    """
    if run_id:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", run_id.strip())
        return cleaned.strip("-") or "run"
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def run_dir(benchmark_name: str, reviewer: str, run_id: str) -> Path:
    """Directory for a run: results/<benchmark>/<reviewer>/<run_id>/.

    Review results (<safe_id>.json) and evaluation metrics (metrics_<reviewer>_<ts>.json)
    are stored here so outputs from the same run stay together.
    """
    return reviewer_results_dir(benchmark_name, reviewer) / run_id


def list_run_dirs(benchmark_name: str, reviewer: str) -> List[Path]:
    """List runs for a benchmark and reviewer in ascending name (timestamp) order."""
    base = reviewer_results_dir(benchmark_name, reviewer)
    if not base.is_dir():
        return []
    return sorted((path for path in base.iterdir() if path.is_dir()), key=lambda p: p.name)


def latest_run_dir(benchmark_name: str, reviewer: str) -> Optional[Path]:
    """Return the latest run directory by name, or None if there are no runs."""
    runs = list_run_dirs(benchmark_name, reviewer)
    return runs[-1] if runs else None


def metrics_run_dir(benchmark_name: str, reviewer: str, run_id: str) -> Path:
    """Evaluation metrics directory: metrics/<benchmark>/<reviewer>/<run_id>/.

    Mirrors the results layout with the same run_id, keeping outputs separate but linked.
    """
    return DEFAULT_METRICS_DIR / benchmark_name / reviewer / run_id

# Review result filenames: <safe_id>.json, where safe_id = instance_id.replace("/", "__").
RESULT_FILE_SUFFIX = ".json"
PARTIAL_FILE_SUFFIX = ".partial.json"

# Environment variables required by the OCR reviewer.
OCR_URL_VAR = "OCR_LLM_URL"
OCR_TOKEN_VAR = "OCR_LLM_TOKEN"
OCR_MODEL_VAR = "OCR_LLM_MODEL"
OCR_USE_ANTHROPIC_VAR = "OCR_USE_ANTHROPIC"
OCR_REQUIRED_ENV_VARS: List[str] = [OCR_URL_VAR, OCR_TOKEN_VAR, OCR_MODEL_VAR]

# Environment variables required by the Claude reviewer.
CLAUDE_URL_VAR = "ANTHROPIC_BASE_URL"
CLAUDE_TOKEN_VAR = "ANTHROPIC_AUTH_TOKEN"
CLAUDE_MODEL_VAR = "ANTHROPIC_MODEL"
CLAUDE_REQUIRED_ENV_VARS: List[str] = [CLAUDE_URL_VAR, CLAUDE_TOKEN_VAR, CLAUDE_MODEL_VAR]

# Claude Code built-in API retry configuration.
CLAUDE_MAX_RETRIES_VAR = "CLAUDE_CODE_MAX_RETRIES"
CLAUDE_MAX_RETRIES_DEFAULT = 10

# StopFailure hook configuration.
HOOKS_DIR = EVALUATION_DIR / "hooks"
STOP_FAILURE_HOOK_SCRIPT = HOOKS_DIR / "on_stop_failure.py"
RETRY_EXHAUSTED_LOG = "retry_exhausted.jsonl"

# MCP findings server for Claude reviews.
MCP_SERVER_SCRIPT = EVALUATION_DIR / "mcp_finding_server.py"
MCP_SERVER_NAME = "findings"
MCP_RESULTS_DIR_ENV_VAR = "REVIEW_RESULTS_DIR"
MCP_INSTANCE_ID_ENV_VAR = "REVIEW_INSTANCE_ID"
MCP_REPORT_TOOL = f"mcp__{MCP_SERVER_NAME}__report"

# Environment variables required by the Codex reviewer.
# The Codex non-interactive mode docs state that CODEX_API_KEY is supported only
# in codex exec. This framework uses codex exec throughout, so use CODEX_API_KEY
# rather than OPENAI_API_KEY.
CODEX_API_KEY_VAR = "CODEX_API_KEY"
CODEX_REQUIRED_ENV_VARS: List[str] = [CODEX_API_KEY_VAR]

# Optional model name: written as a top-level model = "..." in the temporary config.toml.
# If unset, omit that line and use the default model of the default Codex provider.
CODEX_MODEL_VAR = "CODEX_MODEL"

# Optional base_url for a custom gateway or proxy.
# Codex has no CODEX_BASE_URL authentication variable (confirmed by testing);
# custom gateways must use a model_providers table and the top-level model_provider.
CODEX_GATEWAY_URL_VAR = "CODEX_GATEWAY_URL"

# The provider ID is both the top-level model_provider value and the table name
# [model_providers."<id>"]. TOML table names with special characters need double
# quotes (already handled in write_codex_config).
CODEX_PROVIDER_ID = "gateway"

# MCP findings server for Codex reviews (a Codex-specific schema, different from Claude's).
# Finding fields: {file, start_line, end_line, severity, summary, description}
CODEX_MCP_SERVER_SCRIPT = EVALUATION_DIR / "mcp_codex_finding_server.py"
CODEX_MCP_SERVER_NAME = "findings"  # Shares Claude's namespace safely; they do not run together.

# Evaluation-specific configuration (LLM as a Judge), independent of OCR/Claude;
# mapped to the variable names read by judge before importing that module.
JUDGE_BASE_URL_VAR = "JUDGE_BASE_URL"
JUDGE_API_KEY_VAR = "JUDGE_API_KEY"
JUDGE_MODEL_VAR = "JUDGE_MODEL"
JUDGE_USE_MOCK_VAR = "JUDGE_USE_MOCK"
# Judge concurrency limit for file-level semantic matching (read at judge import; default 3).
JUDGE_CONCURRENCY_VAR = "JUDGE_CONCURRENCY"


def safe_id(instance_id: str) -> str:
    """instance_id -> safe result filename ID (replace / with __)."""
    return instance_id.replace("/", "__")


def result_path(results_dir: Path, instance_id: str) -> Path:
    return Path(results_dir) / f"{safe_id(instance_id)}{RESULT_FILE_SUFFIX}"


def partial_path(results_dir: Path, instance_id: str) -> Path:
    return Path(results_dir) / f"{safe_id(instance_id)}{PARTIAL_FILE_SUFFIX}"


def retry_exhausted_path(results_dir: Path) -> Path:
    """Log path for cases that exhaust retries: <results_dir>/retry_exhausted.jsonl."""
    return Path(results_dir) / RETRY_EXHAUSTED_LOG


def missing_env_vars(required: List[str]) -> List[str]:
    """Return missing environment-variable names so errors can be reported early."""
    return [name for name in required if not os.environ.get(name)]
