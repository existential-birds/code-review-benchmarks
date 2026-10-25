"""Evaluation core: match reference and generated comments, compute metrics, and judge semantics.

This module is self-contained and requires no resources outside the framework.
Semantic matching supports two modes:
- LLM: ask a judge whether comments express the same concern or suggestion.
- Mock: approximate with local text similarity (no dependencies or cost; for workflow validation only).

Configuration variable names come from config (JUDGE_BASE_URL / JUDGE_API_KEY /
JUDGE_MODEL / JUDGE_USE_MOCK) and are read directly without intermediate mapping:
  JUDGE_USE_MOCK=true or missing JUDGE_API_KEY -> Mock mode
  Otherwise -> LLM mode (JUDGE_BASE_URL / JUDGE_API_KEY / JUDGE_MODEL)

Two matching paths:
- evaluate_file_level_semantics (current default): group by file and send each
  generated comment with all reference comments in that file to the judge in one
  call to determine coverage, supporting one-to-many matches.
- evaluate_comments (deprecated): match comments individually through four stages,
  path -> side -> line(k) -> semantic; used only with --use-deprecated-score.
"""
from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import re
from collections import defaultdict
from typing import Any, Dict, List

from typing_extensions import deprecated

import config

# Enable local mock semantic matching when JUDGE_API_KEY is missing for zero-configuration validation.
USE_MOCK_LLM = (
    os.getenv(config.JUDGE_USE_MOCK_VAR, "").lower() == "true"
    or not os.getenv(config.JUDGE_API_KEY_VAR)
)

# Semantic matching request count (both mock and LLM).
llm_request_count = 0

# File-level matching calls the judge concurrently with asyncio.gather; cap
# concurrency to prevent bursts. JUDGE_CONCURRENCY sets the limit (default 3),
# with invalid values falling back to the default.
def _judge_concurrency() -> int:
    try:
        return max(1, int(os.getenv(config.JUDGE_CONCURRENCY_VAR, "3")))
    except ValueError:
        return 3


_llm_semaphore = asyncio.Semaphore(_judge_concurrency())

# Initialize the LLM client lazily so mock mode does not require network configuration.
_llm_client = None


def _get_llm_client():
    global _llm_client
    if _llm_client is None:
        from openai import AsyncOpenAI

        _llm_client = AsyncOpenAI(
            base_url=os.getenv(config.JUDGE_BASE_URL_VAR),
            api_key=os.getenv(config.JUDGE_API_KEY_VAR),
        )
    return _llm_client


def _mock_semantic_match(reference_note: str, generated_note: str) -> bool:
    """Approximate semantics locally using sequence similarity and word overlap, without an external LLM."""
    sequence_ratio = difflib.SequenceMatcher(
        None, reference_note.lower(), generated_note.lower()
    ).ratio()

    reference_words = set(re.findall(r"[a-zA-Z_]{3,}", reference_note.lower()))
    generated_words = set(re.findall(r"[a-zA-Z_]{3,}", generated_note.lower()))
    if reference_words and generated_words:
        jaccard = len(reference_words & generated_words) / len(reference_words | generated_words)
    else:
        jaccard = 0.0

    return sequence_ratio >= 0.4 or jaccard >= 0.3


@deprecated(
    "Legacy per-comment matching function, used only with --use-deprecated-score; "
    "file-level matching uses match_llm_file_level"
)
async def match_semantic(reference_note: str, generated_note: str) -> Dict[str, Any]:
    """Determine whether two comments express the same concern or suggestion.

    Deprecated: legacy per-comment matching function, used only by evaluate_comments.
    See match_llm_file_level for file-level matching.
    """
    global llm_request_count
    llm_request_count += 1

    if USE_MOCK_LLM:
        is_similar = _mock_semantic_match(reference_note, generated_note)
        return {"is_similar": is_similar, "reason": f"[mock] matched={is_similar}"}

    prompt = (
        "-Role-\n"
        "You are an expert code reviewer assistant specialized in analyzing and "
        "comparing code review comments.\n\n"
        "-Task-\n"
        "Determine whether two given review comments express the same concern or "
        "suggestion. Ignore differences in wording, tone, or formatting—focus solely "
        "on semantic equivalence of the underlying issue. If the core intent and "
        'technical substance are identical, answer "yes"; otherwise, answer "no".\n\n'
        f"Review Comment 1:\n{reference_note}\n\n"
        f"Review Comment 2:\n{generated_note}\n\n"
        "-Task-\n"
        "Determine whether the two review comments given above express the same concern or "
        "suggestion. Ignore differences in wording, tone, or formatting—focus "
        "solely on semantic equivalence of the underlying issue. If the core intent and "
        'technical substance are identical, answer "yes"; otherwise, answer "no".\n\n'
        "Your answer:"
    )

    try:
        response = await _get_llm_client().chat.completions.create(
            model=os.getenv(config.JUDGE_MODEL_VAR, "multiline_judge_model"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=40000,
            top_p=0.95,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        result_text = response.choices[0].message.content.strip().lower()
        is_similar = (
            "yes" in result_text
            or "similar" in result_text
            or "same" in result_text
            or "identical" in result_text
            or "equivalent" in result_text
        ) and ("no" not in result_text.split("yes")[0] if "yes" in result_text else True)
        return {"is_similar": is_similar, "reason": result_text}
    except Exception as error:  # noqa: BLE001 - A single judgment failure must not abort evaluation.
        logging.error("Judge call failed: %s", error)
        return {"is_similar": False, "reason": f"Evaluation error: {error}"}


def diff_location_is_same(
    from_line1: int, from_line2: int, to_line1: int, to_line2: int, k: int = 1
) -> bool:
    """Check whether two line ranges overlap or their minimum distance is at most k."""
    has_overlap = not (from_line1 > to_line2 or from_line2 < to_line1)
    if has_overlap:
        min_distance = 0
    else:
        min_distance = min(abs(from_line1 - to_line2), abs(from_line2 - to_line1))
    return min_distance <= k


def _normalize_path(path: str) -> str:
    """Normalize a path to use / separators."""
    if not path:
        return ""
    return path.replace("\\/", "/").replace("\\", "/")


def _strip_thinking_tags(text: str) -> str:
    """Remove reasoning tags from a comment (e.g. <details>...</details>)."""
    if not text:
        return ""
    cleaned = re.sub(r"<details>.*?</details>", "", text, flags=re.DOTALL)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


@deprecated(
    "Legacy per-comment matching, used only with --use-deprecated-score and slated "
    "for removal in a future version; use evaluate_file_level_semantics instead"
)
async def evaluate_comments(
    reference_comments: List[Dict[str, Any]],
    generated_comments: List[Dict[str, Any]],
    k: int = 1,
) -> List[Dict[str, Any]]:
    """Evaluate reference comments individually for a single review case.

    Deprecated: legacy per-comment matching, used only with --use-deprecated-score
    and slated for removal in a future version. The current default is
    evaluate_file_level_semantics.

    For each reference comment, filter generated comments by path -> side -> line(k)
    -> semantic, updating the reference's line_match / semantic_match / matched_note
    fields in place.

    Deduplication: each generated comment can contribute at most one line match and
    one semantic match.
    """
    matched_gen_ids_by_line: set = set()
    matched_gen_ids_by_semantic: set = set()

    for ref_comment in reference_comments:
        if not isinstance(ref_comment, dict):
            continue
        # Initialize this path's fields here, including for comments skipped for missing note.
        # Constructors do not prepopulate them.
        ref_comment["line_match"] = False
        ref_comment["semantic_match"] = False
        ref_comment["matched_note"] = ""
        ref_note = ref_comment.get("note", "")
        if not ref_note:
            continue
        ref_note_cleaned = _strip_thinking_tags(ref_note)
        if not ref_note_cleaned:
            continue

        ref_path = _normalize_path(ref_comment.get("path", ""))
        ref_side = ref_comment.get("side")
        ref_from_line = ref_comment.get("from_line")
        ref_to_line = ref_comment.get("to_line")

        line_matched = False
        semantic_matched = False
        matched_note_text = ""

        for gen_idx, gen_comment in enumerate(generated_comments):
            if not isinstance(gen_comment, dict):
                continue
            gen_note = gen_comment.get("note", "")
            if not gen_note:
                continue
            gen_note_cleaned = _strip_thinking_tags(gen_note)
            if not gen_note_cleaned:
                continue

            gen_path = _normalize_path(gen_comment.get("path", ""))
            gen_side = gen_comment.get("side")
            gen_from_line = gen_comment.get("from_line")
            gen_to_line = gen_comment.get("to_line")

            # Stage 1: file path matching.
            if ref_path and gen_path and ref_path != gen_path:
                continue

            # Stage 2: diff side matching.
            if ref_side is not None and gen_side is not None and ref_side != gen_side:
                continue

            # Stage 3: line matching, line(k).
            if all(x is not None for x in [ref_from_line, ref_to_line, gen_from_line, gen_to_line]):
                if not diff_location_is_same(
                    ref_from_line, ref_to_line, gen_from_line, gen_to_line, k
                ):
                    continue

            # Line match found; each generated comment can contribute only once.
            if not line_matched and gen_idx not in matched_gen_ids_by_line:
                line_matched = True
                matched_gen_ids_by_line.add(gen_idx)

            # Skip generated comments already matched semantically.
            if gen_idx in matched_gen_ids_by_semantic:
                continue

            # Stage 4: semantic matching.
            similarity = await match_semantic(ref_note_cleaned, gen_note_cleaned)
            if similarity.get("is_similar", False):
                semantic_matched = True
                matched_note_text = gen_note
                matched_gen_ids_by_semantic.add(gen_idx)
                break

        ref_comment["line_match"] = line_matched
        ref_comment["semantic_match"] = semantic_matched
        ref_comment["matched_note"] = matched_note_text

    return reference_comments


async def match_llm_file_level(
    gen_note: str,
    gen_lines: str,
    ref_notes: List[str],
    ref_lines: List[str],
    file_path: str,
) -> Dict[str, Any]:
    """Match one generated comment against all references in a file; return covered indices.

    Return {"matched": [1-based indices...], "reason": str}. In mock mode, use
    pairwise local similarity within the file without calling an LLM.
    """
    global llm_request_count
    llm_request_count += 1

    if USE_MOCK_LLM:
        matched = [
            i + 1
            for i, ref_note in enumerate(ref_notes)
            if _mock_semantic_match(ref_note, gen_note)
        ]
        return {"matched": matched, "reason": f"[mock] matched={matched}"}

    numbered_refs = "\n".join(
        f"{i + 1}. [L{ref_lines[i]}] {note}" for i, note in enumerate(ref_notes)
    )

    prompt = f"""
-Role-

You are an expert code reviewer comparing review comments for semantic equivalence.

-Task-

Given a Generated Comment and a numbered list of Reference Comments (all from the same file: {file_path}), determine which Reference Comments express the same concern or suggestion as the Generated Comment.

Focus solely on semantic equivalence of the underlying issue — ignore differences in wording, tone, formatting, or exact line numbers. If the Generated Comment addresses the same core problem as a Reference Comment, include that Reference Comment's number.

-Generated Comment-
[L{gen_lines}] {gen_note}

-Reference Comments-
{numbered_refs}

-Output-

For each Reference Comment, briefly state whether it matches and why (one line each).
Then return a JSON array of matched numbers on the LAST line. If none match, return [].

Example:
1. Match — both flag the uninitialized variable risk
2. No match — ref discusses naming, generated discusses null check
3. Match — same concern about missing error handling

[1, 3]

Your answer:
"""

    async with _llm_semaphore:
        try:
            response = await _get_llm_client().chat.completions.create(
                model=os.getenv(config.JUDGE_MODEL_VAR, "file_level_judge_model"),
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=3000,
                top_p=0.95,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            result_text = (response.choices[0].message.content or "").strip()
            matches = re.findall(r"\[[\d,\s]*\]", result_text)
            if matches:
                indices = json.loads(matches[-1])
                valid_indices = [
                    i
                    for i in indices
                    if isinstance(i, int) and 1 <= i <= len(ref_notes)
                ]
                return {"matched": valid_indices, "reason": result_text}
            return {"matched": [], "reason": result_text}
        except Exception as error:  # noqa: BLE001 - A single judgment failure must not abort evaluation.
            logging.error("File-level judge call failed: %s", error)
            return {"matched": [], "reason": f"Evaluation error: {error}"}


async def evaluate_file_level_semantics(
    reference_comments: List[Dict[str, Any]],
    generated_comments: List[Dict[str, Any]],
) -> tuple[int, int, List[Dict[str, Any]]]:
    """File-level semantic matching (the current default metric system).

    Group by file path. Send each generated comment with all reference comments
    from that file to the judge in one call to determine coverage, allowing one
    generated comment to cover multiple references. Update each reference's
    file_semantic_match field in place.

    Returns:
        (unique covered reference count, count of generated comments hitting
        previously uncovered ground truth, generated-comment match records). Each record has
        note / path / from_line / to_line / matched_gt_indices / judge_reason /
        hit_new_gt. matched_gt_indices indexes reference_comments. hit_new_gt is
        False when all matched references were already covered by earlier comments
        in generation order; these duplicate hits do not count in the second return
        value. Also record generated comments in files without references (empty
        matched_gt_indices, no judge call). Generated comments without note or path
        are neither evaluated nor recorded.
    """
    ref_by_path: Dict[str, List] = defaultdict(list)
    for idx, ref in enumerate(reference_comments):
        if not isinstance(ref, dict):
            continue
        ref_note = ref.get("note", "")
        if not ref_note:
            continue
        ref_note_cleaned = _strip_thinking_tags(ref_note)
        if not ref_note_cleaned:
            continue
        path = _normalize_path(ref.get("path", ""))
        if not path:
            continue
        from_line = ref.get("from_line")
        to_line = ref.get("to_line")
        line_str = f"{from_line}-{to_line}" if from_line and to_line else "?"
        ref_by_path[path].append((idx, ref_note_cleaned, line_str))

    gen_by_path: Dict[str, List] = defaultdict(list)
    for gen in generated_comments:
        if not isinstance(gen, dict):
            continue
        gen_note = gen.get("note", "")
        if not gen_note:
            continue
        gen_note_cleaned = _strip_thinking_tags(gen_note)
        if not gen_note_cleaned:
            continue
        path = _normalize_path(gen.get("path", ""))
        if not path:
            continue
        from_line = gen.get("from_line")
        to_line = gen.get("to_line")
        line_str = f"{from_line}-{to_line}" if from_line and to_line else "?"
        gen_by_path[path].append(
            {
                "note_cleaned": gen_note_cleaned,
                "line_str": line_str,
                "record": {
                    "note": gen_note,
                    "path": path,
                    "from_line": from_line,
                    "to_line": to_line,
                    "matched_gt_indices": [],
                    "judge_reason": "",
                },
            }
        )

    matched_ref_indices: set = set()

    async def _file_level_task(entry: Dict[str, Any], ref_list: List, path: str):
        result = await match_llm_file_level(
            entry["note_cleaned"],
            entry["line_str"],
            [note for _, note, _ in ref_list],
            [lines for _, _, lines in ref_list],
            path,
        )
        # Map the LLM's 1-based ref_list indices back to reference_comments indices.
        matched = [ref_list[num - 1][0] for num in result["matched"]]
        return entry["record"], matched, str(result.get("reason", ""))

    tasks = []
    for path, ref_list in ref_by_path.items():
        for entry in gen_by_path.get(path, []):
            tasks.append(_file_level_task(entry, ref_list, path))

    for record, matched, reason in await asyncio.gather(*tasks):
        record["matched_gt_indices"] = matched
        record["judge_reason"] = reason[:500]
        matched_ref_indices.update(matched)

    for idx, ref in enumerate(reference_comments):
        if isinstance(ref, dict):
            ref["file_semantic_match"] = idx in matched_ref_indices

    gen_records = [
        entry["record"] for entries in gen_by_path.values() for entry in entries
    ]

    # Greedy deduplication in generation order: if all matched references were
    # already covered, exclude the duplicate hit from the precision numerator.
    covered_gt_indices: set = set()
    matched_generated_count = 0
    for record in gen_records:
        new_hits = set(record["matched_gt_indices"]) - covered_gt_indices
        record["hit_new_gt"] = bool(new_hits)
        if new_hits:
            matched_generated_count += 1
            covered_gt_indices.update(record["matched_gt_indices"])

    return len(matched_ref_indices), matched_generated_count, gen_records


def compute_cr_statistics(
    comments: List[Dict[str, Any]],
    generated_count: int,
    matched_generated_count: int = 0,
) -> Dict[str, Any]:
    """Compute statistics for a single review case from evaluated comments.

    file_semantic_* are the current default metrics (file-level semantic matching):
    precision = generated comments hitting previously uncovered ground truth /
    total generated comments. Duplicate hits do not contribute to the numerator,
    supplied by matched_generated_count. Recall = covered references
    (file_semantic_match_count) / total references.
    Deprecated: line_* / semantic_* belong to the legacy per-comment metrics and
    are meaningful only after evaluate_comments writes them in --use-deprecated-score mode.
    """
    total_expected = len([c for c in comments if isinstance(c, dict) and c.get("note")])
    line_match_count = sum(
        1 for c in comments if isinstance(c, dict) and c.get("line_match", False)
    )
    semantic_match_count = sum(
        1 for c in comments if isinstance(c, dict) and c.get("semantic_match", False)
    )
    file_semantic_match_count = sum(
        1
        for c in comments
        if isinstance(c, dict) and c.get("file_semantic_match", False)
    )
    return {
        "expected_notes": total_expected,
        "generated_notes": generated_count,
        "line_match_count": line_match_count,
        "semantic_match_count": semantic_match_count,
        "file_semantic_match_count": file_semantic_match_count,
        "line_match_rate": round(line_match_count / generated_count, 3) if generated_count else 0.0,
        "semantic_match_rate": round(semantic_match_count / generated_count, 3)
        if generated_count
        else 0.0,
        "file_semantic_match_rate": round(matched_generated_count / generated_count, 3)
        if generated_count
        else 0.0,
        "line_recall_rate": round(line_match_count / total_expected, 3) if total_expected else 0.0,
        "semantic_recall_rate": round(semantic_match_count / total_expected, 3)
        if total_expected
        else 0.0,
        "file_semantic_recall_rate": round(file_semantic_match_count / total_expected, 3)
        if total_expected
        else 0.0,
    }


def print_llm_request_statistics() -> None:
    """Print semantic matching request statistics."""
    print("\n" + "=" * 50)
    print("Semantic matching request statistics")
    print("=" * 50)
    print(f"Mode: {'Mock (local similarity)' if USE_MOCK_LLM else 'LLM (live judge)'}")
    print(f"Total requests: {llm_request_count}")
    print("=" * 50 + "\n")
