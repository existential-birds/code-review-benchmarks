"""Convert AACR-Bench JSON to the evaluation framework's standard format.

Read raw data, produce ReviewInstances conforming to schema.py, and write standard JSONL.

AACR-Bench raw field mapping:
  githubPrUrl (github.com/<owner>/<name>/pull/N) -> repo (owner/name)
  source_commit                                  -> base_commit
  target_commit                                  -> head_commit
  owner__name@<first 7 target characters>          -> instance_id (derived from repo/commit; no existing ID)
  comments[]                                      -> reference_comments[] (keep all AI and human comments)
    path               -> path
    note               -> text
    from_line/to_line  -> start_line / end_line (inclusive line range)
    side               -> side ("left" / "right", the side of the diff the comment refers to)

By convention, raw data lives under benchmark/AACR-Bench/ and converted output is
named after the benchmark: data/aacr_bench.jsonl. These defaults apply when --input
and --output are omitted, avoiding long paths. If the default input is missing,
download, validate, and cache it locally using positive_samples.meta.json.

Usage (from evaluation/ with the virtual environment activated):
    python -m converters.aacr_bench [--limit 30] [--validate]
    # Or specify paths explicitly:
    python -m converters.aacr_bench \
        --input benchmark/AACR-Bench/positive_samples.json \
        --output data/aacr_bench.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

# Allow direct script execution by adding evaluation/ to sys.path for top-level imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from schema import ReferenceComment, ReviewInstance, load_instances  # noqa: E402

# Benchmark conventions: raw directory name and standard dataset key.
BENCHMARK_NAME = "AACR-Bench"
BENCHMARK_KEY = "aacr_bench"
DEFAULT_INPUT = config.benchmark_raw_dir(BENCHMARK_NAME) / "positive_samples.json"
DEFAULT_OUTPUT = config.dataset_path(BENCHMARK_KEY)

# Parse owner/name from githubPrUrl, e.g. https://github.com/FreeCAD/FreeCAD/pull/19411 -> FreeCAD/FreeCAD.
_GITHUB_PR_PATTERN = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/\d+", re.IGNORECASE)


def _load_meta(meta_path: Path) -> Dict[str, Any]:
    """Read the matching .meta.json; return {} if missing, or exit on a parse error."""
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as error:
        raise SystemExit(f"Failed to parse metadata JSON {meta_path}: {error}")


def _sha256(path: Path) -> str:
    """Compute a file's SHA-256 in chunks to avoid loading large files into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_raw_file(input_path: Path) -> Path:
    """Fetch and validate raw data using the matching .meta.json; return its actual path.

    Locate .meta.json using input_path's stem. Its optional filename field determines
    the data filename, falling back to input_path.name, so metadata and data filenames
    may differ. Other fields are url (HTTPS download URL) and sha256 (optional
    integrity checksum).

    Reuse a local file if its checksum passes or metadata has no sha256. Otherwise,
    download from url to the target path in chunks via a temporary file and atomic
    os.replace, so an interrupted download cannot be reused as a valid cache entry.
    Then verify SHA-256 integrity. Exit if both the file and metadata are missing.
    """
    input_path = Path(input_path)
    meta_path = input_path.with_name(f"{input_path.stem}.meta.json")
    meta = _load_meta(meta_path)

    data_path = input_path.with_name(meta.get("filename") or input_path.name)

    expected_sha = meta.get("sha256")
    if data_path.exists() and (expected_sha is None or _sha256(data_path) == expected_sha):
        return data_path

    url = meta.get("url")
    if not url:
        if data_path.exists():
            raise SystemExit(
                f"Local file checksum failed and no download URL is available: {data_path}\n"
                f"Expected sha256={expected_sha}\n  meta: {meta_path}"
            )
        raise SystemExit(
            f"Input file is missing and no download metadata is available: {data_path}\n"
            f"Expected matching .meta.json: {meta_path}"
        )

    if not url.startswith("https://"):
        raise SystemExit(
            f"Only https:// download URLs are supported, got: {url}\n  meta: {meta_path}"
        )

    data_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[fetch] Local file missing or checksum failed: {data_path}; downloading from:\n  {url}")
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            _stream_to_file(response, data_path)
    except urllib.error.HTTPError as error:
        raise SystemExit(
            f"Download failed: HTTP {error.code} {error.reason}\n  URL: {url}"
        )
    except Exception as error:
        raise SystemExit(
            f"Failed to download raw data: {error}\n  URL: {url}\n"
            f"Check your network, or manually download the data to {data_path} and retry."
        )

    if expected_sha is not None:
        actual_sha = _sha256(data_path)
        if actual_sha != expected_sha:
            raise SystemExit(
                f"Downloaded content failed validation:\n  expected sha256={expected_sha}\n"
                f"  actual sha256={actual_sha}\n  URL: {url}"
            )

    return data_path


def _stream_to_file(response: Any, target: Path) -> None:
    """Stream chunks to a temporary file, then atomically replace the target with os.replace.

    urlopen raises HTTPError for 4xx/5xx responses (handled by the caller), so responses
    reaching here have 2xx status. Read chunks as in _sha256 to avoid loading large
    files into memory. Remove the temporary file on any exception.
    """
    tmp_path = target.with_name(f"{target.name}.part")
    try:
        with tmp_path.open("wb") as handle:
            for chunk in iter(lambda: response.read(1 << 16), b""):
                handle.write(chunk)
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def parse_repo_from_pr_url(pr_url: Any) -> Optional[str]:
    """Parse owner/name from githubPrUrl, returning None if parsing fails."""
    if not isinstance(pr_url, str):
        return None
    match = _GITHUB_PR_PATTERN.search(pr_url)
    if not match:
        return None
    return f"{match.group(1)}/{match.group(2)}"


def convert_record(record: Dict[str, Any]) -> Optional[ReviewInstance]:
    """Convert an AACR-Bench record to a ReviewInstance; return None if key fields are missing."""
    if not isinstance(record, dict):
        return None

    repo = parse_repo_from_pr_url(record.get("githubPrUrl"))
    base_commit = record.get("source_commit")
    head_commit = record.get("target_commit")
    # instance_id: replace / in repo with __ and take the first 7 characters of head_commit.
    instance_id = (
        f"{repo.replace('/', '__')}@{str(head_commit)[:7]}"
        if repo and head_commit
        else None
    )

    if not all([instance_id, repo, base_commit, head_commit]):
        return None

    reference_comments: List[ReferenceComment] = []
    for raw in record.get("comments", []) or []:
        if not isinstance(raw, dict):
            continue
        path = raw.get("path")
        text = raw.get("note")
        if not path or not text or not str(text).strip():
            continue
        # AACR-Bench uses from_line/to_line for reference-comment line ranges.
        start_line = raw.get("from_line")
        end_line = raw.get("to_line")
        # AACR-Bench uses side for the diff side the comment refers to ("left" / "right").
        side = raw.get("side")
        if side is not None:
            side = str(side).strip()
        reference_comments.append(
            ReferenceComment(
                path=str(path).strip(),
                text=str(text).strip(),
                start_line=start_line,
                end_line=end_line,
                side=side,
            )
        )

    return ReviewInstance(
        instance_id=str(instance_id).strip(),
        repo=str(repo).strip(),
        base_commit=str(base_commit).strip(),
        head_commit=str(head_commit).strip(),
        reference_comments=reference_comments,
    )


def convert_file(
    input_path: Path,
    output_path: Path,
    limit: Optional[int],
    seed: Optional[int] = None,
) -> int:
    """Read an AACR-Bench JSON array, shuffle, apply limit, and write standard JSONL.

    Shuffle all raw records before applying limit to select a random subset rather
    than the first N records. A non-None seed makes sampling reproducible for a fixed
    evaluation subset.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    input_path = ensure_raw_file(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        records = json.loads(input_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SystemExit(f"Failed to parse input JSON: {error}")
    if not isinstance(records, list):
        raise SystemExit(
            f"AACR-Bench raw data must be a JSON array, got {type(records).__name__}"
        )

    rng = random.Random(seed)
    rng.shuffle(records)
    if limit is not None:
        records = records[:limit]

    written = 0
    skipped = 0
    with output_path.open("w", encoding="utf-8") as writer:
        for record in records:
            instance = convert_record(record)
            if instance is None:
                skipped += 1
                continue
            writer.write(json.dumps(instance.to_dict(), ensure_ascii=False) + "\n")
            written += 1

    print(f"Conversion complete: wrote {written} records, skipped {skipped} -> {output_path}")
    return written


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="AACR-Bench JSON -> standard format converter")
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Path to raw AACR-Bench JSON (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output path for standard JSONL (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Convert only the first N records after shuffling (random sample)"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Shuffle seed for reproducible sampling (default: random each time)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Reload and validate the converted output with the schema",
    )
    args = parser.parse_args(argv)

    convert_file(args.input, args.output, args.limit, args.seed)

    if args.validate:
        instances = load_instances(args.output)
        print(f"Validation passed: {len(instances)} standard-format review cases loaded by the schema")
    return 0


if __name__ == "__main__":
    sys.exit(main())
