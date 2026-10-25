"""Evaluation stage: compare standard reference data with review results for each case.

Compute file-level semantic matching metrics (precision / recall / balanced score)
by default, using judge.evaluate_file_level_semantics. With --use-deprecated-score,
use legacy per-comment metrics (P/R/F1 through path -> side -> line(k) -> semantic;
deprecated).

The judge (LLM as a Judge) is independent of OCR/Claude. Configure it in
evaluation/.env with JUDGE_BASE_URL / JUDGE_API_KEY / JUDGE_MODEL / JUDGE_USE_MOCK.
The judge module reads these names directly from config; this module only loads
.env before importing judge so the variables are available.

Line-number normalization for consistent comparison:
- Reference: the standard format already uses inclusive [start_line, end_line] ranges.
- Claude: a single line is both the start and end.
- OCR: use start_line / end_line directly as the start / end.
- Codex: use start_line / end_line directly, as with OCR.

Mapping to note (text for semantic comparison):
- Reference: text
- OCR: content
- Claude: summary + "\n" + failure_scenario
- Codex: summary + "\n" + description
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from schema import ReviewInstance
from typing_extensions import deprecated


def _bootstrap_judge_env() -> None:
    """Load evaluation/.env before importing judge so JUDGE_* variables are available.

    Call before importing judge: its USE_MOCK_LLM and LLM client configuration
    are fixed from environment variables at import time; setting them later has
    no effect. judge reads the JUDGE_* names defined by config directly, without
    intermediate mapping.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_file = config.EVALUATION_DIR / ".env"
    if env_file.exists():
        load_dotenv(env_file)


_bootstrap_judge_env()

# Local evaluation core (imported after loading the judge environment).
from judge import (  # noqa: E402
    USE_MOCK_LLM,
    compute_cr_statistics,
    evaluate_comments,
    evaluate_file_level_semantics,
    print_llm_request_statistics,
)

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        return iterable


def _normalize_line_range(
    from_line: Optional[int], to_line: Optional[int]
) -> Dict[str, Optional[int]]:
    if from_line is None and to_line is None:
        return {"from_line": None, "to_line": None}
    if from_line is None:
        from_line = to_line
    if to_line is None:
        to_line = from_line
    if from_line > to_line:
        from_line, to_line = to_line, from_line
    return {"from_line": from_line, "to_line": to_line}


def build_reference_comments(instance: ReviewInstance) -> List[Dict[str, Any]]:
    """Convert standard reference comments to the judge's comparison format."""
    comments = []
    for ref in instance.reference_comments:
        line_range = _normalize_line_range(ref.start_line, ref.end_line)
        comments.append(
            {
                "note": ref.text,
                "path": ref.path,
                "side": ref.side,
                "from_line": line_range["from_line"],
                "to_line": line_range["to_line"],
            }
        )
    return comments


def build_target_comments_from_claude(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert Claude review_output to comments for evaluation; line is both start and end."""
    comments = []
    for finding in result.get("review_output", []) or []:
        if not isinstance(finding, dict):
            continue
        summary = (finding.get("summary") or "").strip()
        failure_scenario = (finding.get("failure_scenario") or "").strip()
        note = "\n".join(part for part in [summary, failure_scenario] if part).strip()
        if not note:
            continue
        line_range = _normalize_line_range(finding.get("line"), finding.get("line"))
        comments.append(
            {
                "note": note,
                "path": finding.get("file", ""),
                # All three reviewers inspect the right side (new code), so side is fixed to "right".
                "side": "right",
                "from_line": line_range["from_line"],
                "to_line": line_range["to_line"],
            }
        )
    return comments


def build_target_comments_from_codex(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert Codex review_output to comments, using start_line/end_line directly.

    Unlike Claude, note uses description instead of failure_scenario, and line
    numbers use an inclusive start_line/end_line range instead of a single line.
    Ignore severity (retained only in the archived result).
    """
    comments = []
    for finding in result.get("review_output", []) or []:
        if not isinstance(finding, dict):
            continue
        summary = (finding.get("summary") or "").strip()
        description = (finding.get("description") or "").strip()
        note = "\n".join(part for part in [summary, description] if part).strip()
        if not note:
            continue
        line_range = _normalize_line_range(
            finding.get("start_line"), finding.get("end_line")
        )
        comments.append(
            {
                "note": note,
                "path": finding.get("file", ""),
                # All three reviewers inspect the right side (new code), so side is fixed to "right".
                "side": "right",
                "from_line": line_range["from_line"],
                "to_line": line_range["to_line"],
            }
        )
    return comments


def build_target_comments_from_ocr(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert OCR review.comments to comments for evaluation, using start_line/end_line directly."""
    review = result.get("review")
    if not isinstance(review, dict):
        return []
    comments = []
    for finding in review.get("comments", []) or []:
        if not isinstance(finding, dict):
            continue
        note = (finding.get("content") or "").strip()
        if not note:
            continue
        line_range = _normalize_line_range(finding.get("start_line"), finding.get("end_line"))
        comments.append(
            {
                "note": note,
                "path": finding.get("path", ""),
                # All three reviewers inspect the right side (new code), so side is fixed to "right".
                "side": "right",
                "from_line": line_range["from_line"],
                "to_line": line_range["to_line"],
            }
        )
    return comments


def load_target_comments(
    results_dir: Path, reviewer: str, instance_id: str
) -> Optional[List[Dict[str, Any]]]:
    """Find the result by exact instance_id and build comments; return None if the file is missing."""
    path = config.result_path(results_dir, instance_id)
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            result = json.load(handle)
    except (json.JSONDecodeError, OSError) as error:
        logging.warning("Skipping unreadable or unparseable result file %s: %s", path.name, error)
        return None
    if not isinstance(result, dict):
        logging.warning("Skipping malformed result file (top level is not an object): %s", path.name)
        return None
    builder = {
        "claude": build_target_comments_from_claude,
        "codex": build_target_comments_from_codex,  # Separate implementation, not an alias.
    }.get(reviewer, build_target_comments_from_ocr)
    return builder(result)


def _extract_usage_from_result(
    results_dir: Path, reviewer: str, instance_id: str,
) -> Optional[Dict[str, Any]]:
    """Extract execution duration and token usage from a result file.

    Claude Code: duration_seconds, token_usage.{input_tokens, output_tokens,
                 cache_read_input_tokens, cache_creation_input_tokens}
                 input_tokens excludes cached tokens; total_input sums all three input fields.
    Codex:       duration_seconds, token_usage.{input_tokens, output_tokens,
                 cached_input_tokens (=cache_read_input_tokens), reasoning_output_tokens}
                 input_tokens includes cached tokens (OpenAI convention); do not add cache_read again.
                 Do not add reasoning tokens to the total (not comparable with Claude; archived only).
    OCR:         duration_seconds, review.summary.{input_tokens, output_tokens}
    """
    path = config.result_path(results_dir, instance_id)
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None

    duration = data.get("duration_seconds", 0) or 0

    # Claude and Codex share token_usage field names but account for cached tokens
    # differently, so extract them separately:
    # - Claude (Anthropic): input_tokens excludes cached tokens; add cache_read +
    #   cache_creation to get the full input count.
    # - Codex (OpenAI): input_tokens already includes cached tokens. reviewers/codex.py
    #   mirrors cached_input_tokens as cache_read_input_tokens only to align names;
    #   adding cache_read again would double-count all cached tokens.
    if reviewer == "claude":
        token_usage = data.get("token_usage") or {}
        input_tokens = (
            (token_usage.get("input_tokens", 0) or 0)
            + (token_usage.get("cache_read_input_tokens", 0) or 0)
            + (token_usage.get("cache_creation_input_tokens", 0) or 0)
        )
        output_tokens = token_usage.get("output_tokens", 0) or 0
    elif reviewer == "codex":
        token_usage = data.get("token_usage") or {}
        input_tokens = token_usage.get("input_tokens", 0) or 0
        output_tokens = token_usage.get("output_tokens", 0) or 0
    else:
        review = data.get("review") or {}
        summary = review.get("summary") or {}
        input_tokens = summary.get("input_tokens", 0) or 0
        output_tokens = summary.get("output_tokens", 0) or 0

    return {
        "duration_seconds": duration,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def _compute_balanced_score(precision: float, recall: float) -> float:
    """Harmonic mean of precision and recall (F1, called balanced score in the new metrics)."""
    if (precision + recall) <= 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 3)


def _base_summary(stats: Dict[str, Any]) -> Dict[str, Any]:
    """Case counts and usage statistics shared by both metric systems."""
    evaluated = stats["evaluated_instances"]
    total_duration = stats.get("total_duration_seconds", 0)
    total_input_tokens = stats.get("total_input_tokens", 0)
    total_output_tokens = stats.get("total_output_tokens", 0)
    return {
        "total_instances": stats["total_instances"],
        "candidate_instances": stats["candidate_instances"],
        "evaluated_instances": evaluated,
        "missing_instances": stats["missing_instances"],
        "expected_notes": stats["expected_notes"],
        "generated_notes": stats["generated_notes"],
        "total_duration_seconds": round(total_duration, 2),
        "avg_duration_seconds": round(total_duration / evaluated, 2) if evaluated else 0,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_tokens": total_input_tokens + total_output_tokens,
        "avg_input_tokens": round(total_input_tokens / evaluated) if evaluated else 0,
        "avg_output_tokens": round(total_output_tokens / evaluated) if evaluated else 0,
    }


def _compute_summary(stats: Dict[str, Any]) -> Dict[str, Any]:
    """File-level semantic metrics (current default): precision / recall / balanced score.

    Precision = generated comments hitting previously uncovered ground truth /
    total generated comments (duplicate hits excluded).
    Recall = covered references / total references (deduplicated for one-to-many matches).
    """
    expected = stats["expected_notes"]
    generated = stats["generated_notes"]
    matched_generated = stats["matched_generated_notes"]
    matched_gt = stats["matched_file_semantic_notes"]
    precision = round(matched_generated / generated, 3) if generated else 0.0
    recall = round(matched_gt / expected, 3) if expected else 0.0

    return {
        **_base_summary(stats),
        "matched_generated_notes": matched_generated,
        "matched_file_semantic_notes": matched_gt,
        "file_semantic_match_rate": precision,
        "file_semantic_recall_rate": recall,
        "balanced_score": _compute_balanced_score(precision, recall),
    }


@deprecated(
    "Legacy per-comment metrics (semantic/line P/R/F1), used only with --use-deprecated-score "
    "and slated for removal in a future version; use _compute_summary instead"
)
def _compute_summary_deprecated(stats: Dict[str, Any]) -> Dict[str, Any]:
    """Deprecated: legacy per-comment metrics (semantic/line P/R/F1).

    Used only with --use-deprecated-score and slated for removal in a future version.
    The new workflow uses _compute_summary (file-level semantic matching metrics).
    """
    expected = stats["expected_notes"]
    generated = stats["generated_notes"]
    sem_p = round(stats["matched_semantic_notes"] / generated, 3) if generated else 0.0
    line_p = round(stats["matched_line_notes"] / generated, 3) if generated else 0.0
    sem_r = round(stats["matched_semantic_notes"] / expected, 3) if expected else 0.0
    line_r = round(stats["matched_line_notes"] / expected, 3) if expected else 0.0

    return {
        **_base_summary(stats),
        "matched_line_notes": stats["matched_line_notes"],
        "matched_semantic_notes": stats["matched_semantic_notes"],
        "semantic_match_rate": sem_p,
        "line_match_rate": line_p,
        "semantic_recall_rate": sem_r,
        "line_recall_rate": line_r,
        "semantic_f1": _compute_balanced_score(sem_p, sem_r),
        "line_f1": _compute_balanced_score(line_p, line_r),
    }


async def evaluate(
    instances: List[ReviewInstance],
    results_dir: Path,
    reviewer: str,
    output_dir: Path,
    line_k: int = 1,
    limit: Optional[int] = None,
    round_label: str = "",
    use_deprecated_score: bool = False,
) -> Dict[str, Any]:
    """Compute metrics for a reviewer's results directory, returning and saving the data.

    round_label: use a suffix such as "_round_1" to distinguish filenames across
        multiple evaluation rounds; use an empty string for a single round.
    use_deprecated_score: True selects legacy per-comment metrics (deprecated).
        Defaults to False for file-level semantic metrics (precision/recall/balanced score).
    """
    if not Path(results_dir).is_dir():
        raise FileNotFoundError(f"Results directory does not exist: {results_dir}")
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    candidate = instances[:limit] if limit is not None else instances

    stats: Dict[str, Any] = {
        "total_instances": len(instances),
        "candidate_instances": len(candidate),
        "evaluated_instances": 0,
        "missing_instances": 0,
        "expected_notes": 0,
        "generated_notes": 0,
        "matched_line_notes": 0,
        "matched_semantic_notes": 0,
        "matched_file_semantic_notes": 0,
        "matched_generated_notes": 0,
        "total_duration_seconds": 0.0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
    }
    eval_results: List[Dict[str, Any]] = []
    missing_ids: List[str] = []

    for instance in tqdm(candidate, desc=f"Evaluating {reviewer}", unit="inst"):
        generated = load_target_comments(results_dir, reviewer, instance.instance_id)
        if generated is None:
            missing_ids.append(instance.instance_id)
            continue

        reference = build_reference_comments(instance)
        generated_count = len([c for c in generated if isinstance(c, dict) and c.get("note")])

        gen_match_records: List[Dict[str, Any]] = []
        matched_generated_count = 0
        if use_deprecated_score:
            if reference:
                await evaluate_comments(reference, generated, k=line_k)
        else:
            # The judge returns the precision numerator: generated comments hitting
            # previously uncovered references, excluding duplicate hits.
            _, matched_generated_count, gen_match_records = (
                await evaluate_file_level_semantics(reference, generated)
            )

        cr_stats = compute_cr_statistics(
            reference, generated_count, matched_generated_count
        )
        stats["evaluated_instances"] += 1
        stats["expected_notes"] += cr_stats["expected_notes"]
        stats["generated_notes"] += generated_count
        if use_deprecated_score:
            stats["matched_line_notes"] += cr_stats["line_match_count"]
            stats["matched_semantic_notes"] += cr_stats["semantic_match_count"]
        else:
            stats["matched_file_semantic_notes"] += cr_stats[
                "file_semantic_match_count"
            ]
            stats["matched_generated_notes"] += matched_generated_count

        # Accumulate execution duration and token usage.
        usage = _extract_usage_from_result(results_dir, reviewer, instance.instance_id)
        if usage:
            stats["total_duration_seconds"] += usage["duration_seconds"]
            stats["total_input_tokens"] += usage["input_tokens"]
            stats["total_output_tokens"] += usage["output_tokens"]

        entry: Dict[str, Any] = {
            "instance_id": instance.instance_id,
            "repo": instance.repo,
            "comments": reference,
        }
        if not use_deprecated_score:
            entry["generated_comments"] = gen_match_records
        eval_results.append(entry)

    stats["missing_instances"] = len(missing_ids)
    if missing_ids:
        logging.info(
            "[%s] Of %d candidate cases, %d have no matching result file (skipped, excluded from metrics)",
            reviewer,
            len(candidate),
            len(missing_ids),
        )

    summary = (
        _compute_summary_deprecated(stats)
        if use_deprecated_score
        else _compute_summary(stats)
    )
    result_data = {
        "ex_info": {
            "start_time": datetime.now().isoformat(),
            "results_dir": str(results_dir),
            "reviewer": reviewer,
            "line_k": line_k,
            "limit": limit,
            "score_mode": "deprecated" if use_deprecated_score else "file_semantic",
            "missing_instance_ids": missing_ids,
        },
        "summary": summary,
        "eval_res": eval_results,
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(output_dir) / f"metrics_{reviewer}_{timestamp}{round_label}.json"
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(result_data, handle, ensure_ascii=False, indent=2)
    print(f"\nEvaluation results saved to: {out_path}")
    return result_data


def print_summary(reviewer: str, result_data: Dict[str, Any]) -> None:
    summary = result_data.get("summary", {})
    score_mode = result_data.get("ex_info", {}).get("score_mode", "file_semantic")
    deprecated = score_mode == "deprecated"
    print("\n" + "=" * 56)
    print(f"Evaluation summary ({reviewer})" + (" [deprecated scoring mode]" if deprecated else ""))
    print("=" * 56)
    print(f"Dataset cases   (total_instances):     {summary.get('total_instances')}")
    print(f"Candidate cases (candidate_instances): {summary.get('candidate_instances')}")
    print(f"Evaluated cases (evaluated_instances): {summary.get('evaluated_instances')}")
    print(f"Missing cases   (missing_instances):   {summary.get('missing_instances')}")
    print(f"Reference comments (expected_notes):   {summary.get('expected_notes')}")
    print(f"Generated comments (generated_notes):  {summary.get('generated_notes')}")
    print("-" * 56)
    if deprecated:
        print(f"Semantic matches:                     {summary.get('matched_semantic_notes')}")
        print(f"Line matches:                         {summary.get('matched_line_notes')}")
        print("-" * 56)
        print(f"Semantic precision:  {summary.get('semantic_match_rate')}")
        print(f"Semantic recall:     {summary.get('semantic_recall_rate')}")
        print(f"Semantic F1:         {summary.get('semantic_f1')}")
        print(f"Line precision:      {summary.get('line_match_rate')}")
        print(f"Line recall:         {summary.get('line_recall_rate')}")
        print(f"Line F1:             {summary.get('line_f1')}")
    else:
        print(f"Matched generated comments (precision numerator): {summary.get('matched_generated_notes')}")
        print(f"Covered reference comments (recall numerator):    {summary.get('matched_file_semantic_notes')}")
        print("-" * 56)
        print(f"Precision:       {summary.get('file_semantic_match_rate')}")
        print(f"Recall:          {summary.get('file_semantic_recall_rate')}")
        print(f"Balanced score:  {summary.get('balanced_score')}")
    print("-" * 56)
    total_dur = summary.get("total_duration_seconds", 0)
    avg_dur = summary.get("avg_duration_seconds", 0)
    print(f"Total execution time:    {total_dur}s ({total_dur/60:.1f}min)")
    print(f"Average execution time:  {avg_dur}s ({avg_dur/60:.1f}min)")
    print(f"Total input tokens:      {summary.get('total_input_tokens', 0):,}")
    print(f"Total output tokens:     {summary.get('total_output_tokens', 0):,}")
    print(f"Total tokens:            {summary.get('total_tokens', 0):,}")
    print(f"Average input tokens:    {summary.get('avg_input_tokens', 0):,}")
    print(f"Average output tokens:   {summary.get('avg_output_tokens', 0):,}")
    print("=" * 56)


def judge_mode_description() -> str:
    if USE_MOCK_LLM:
        return "Mock (local similarity)"
    return f"LLM (live judge) | Model: {os.getenv(config.JUDGE_MODEL_VAR, '')}"
