"""Standard data schema and load-time validation for the evaluation framework.

To integrate a benchmark, convert its raw data to the standard format defined here
(JSONL, one ReviewInstance per line). The review and evaluation stages can then
consume it directly without framework changes.

Why use dataclasses and load-time validation:
- Centralize field and type declarations to avoid hardcoded raw field names and
  implicit coupling between stages.
- Validate required fields and line numbers when loading so invalid data fails
  early rather than partway through a review or evaluation.

Standard format (one JSON object per line):
    {
      "instance_id": "psf__requests-5711@9484e13",
      "repo": "psf/requests",
      "base_commit": "5351469...",
      "head_commit": "9484e13...",
      "clone_url": "https://github.com/psf/requests.git",   // Optional; derived from repo if omitted
      "reference_comments": [
        {"path": "setup.py", "start_line": 46, "end_line": 46, "text": "...", "side": "left"}
      ]
    }

Line numbers in reference_comments use inclusive ranges [start_line, end_line].
For a single-line comment, start_line == end_line. null is allowed when no specific
line number is available.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


class SchemaError(ValueError):
    """Raised when schema validation fails, with line or review-case location details."""


@dataclass
class ReferenceComment:
    """A manually annotated reference finding in the standard format."""

    path: str
    text: str
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    side: Optional[str] = None  # "left" or "right": the side of the diff the comment refers to.

    @staticmethod
    def from_dict(data: Dict[str, Any], where: str) -> "ReferenceComment":
        if not isinstance(data, dict):
            raise SchemaError(f"{where}: reference_comment must be an object, got {type(data).__name__}")

        path = data.get("path")
        text = data.get("text")
        if not isinstance(path, str) or not path.strip():
            raise SchemaError(f"{where}: reference_comment.path must be a non-empty string")
        if not isinstance(text, str) or not text.strip():
            raise SchemaError(f"{where}: reference_comment.text must be a non-empty string")

        start_line = _validate_optional_line(data.get("start_line"), f"{where}.start_line")
        end_line = _validate_optional_line(data.get("end_line"), f"{where}.end_line")
        # Normalize to an ordered inclusive range; fill a missing endpoint with the other.
        start_line, end_line = _normalize_line_range(start_line, end_line)

        side = data.get("side")
        if side is not None:
            if not isinstance(side, str) or side.strip() not in ("left", "right"):
                raise SchemaError(f"{where}.side: must be 'left' or 'right' if provided, got {side!r}")
            side = side.strip()

        return ReferenceComment(
            path=path.strip(),
            text=text.strip(),
            start_line=start_line,
            end_line=end_line,
            side=side,
        )

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "text": self.text,
        }
        if self.side is not None:
            result["side"] = self.side
        return result


@dataclass
class ReviewInstance:
    """A review case to review or evaluate, in the standard format."""

    instance_id: str
    repo: str
    base_commit: str
    head_commit: str
    clone_url: Optional[str] = None
    reference_comments: List[ReferenceComment] = field(default_factory=list)

    @property
    def safe_id(self) -> str:
        """Safe ID for result filenames, following the legacy eval/ scripts' convention."""
        return self.instance_id.replace("/", "__")

    @property
    def resolved_clone_url(self) -> str:
        """Clone URL: use the explicit value, or derive a GitHub URL from repo."""
        if self.clone_url:
            return self.clone_url
        return f"https://github.com/{self.repo}.git"

    @staticmethod
    def from_dict(data: Dict[str, Any], where: str) -> "ReviewInstance":
        if not isinstance(data, dict):
            raise SchemaError(f"{where}: each line must be a JSON object, got {type(data).__name__}")

        instance_id = data.get("instance_id")
        repo = data.get("repo")
        base_commit = data.get("base_commit")
        head_commit = data.get("head_commit")

        _require_non_empty_str(instance_id, f"{where}.instance_id")
        _require_non_empty_str(repo, f"{where}.repo")
        _require_non_empty_str(base_commit, f"{where}.base_commit")
        _require_non_empty_str(head_commit, f"{where}.head_commit")

        clone_url = data.get("clone_url")
        if clone_url is not None and (not isinstance(clone_url, str) or not clone_url.strip()):
            raise SchemaError(f"{where}.clone_url: must be a non-empty string if provided")

        raw_comments = data.get("reference_comments", []) or []
        if not isinstance(raw_comments, list):
            raise SchemaError(f"{where}.reference_comments: must be an array")

        reference_comments = [
            ReferenceComment.from_dict(item, f"{where}.reference_comments[{idx}]")
            for idx, item in enumerate(raw_comments)
        ]

        return ReviewInstance(
            instance_id=instance_id.strip(),
            repo=repo.strip(),
            base_commit=base_commit.strip(),
            head_commit=head_commit.strip(),
            clone_url=clone_url.strip() if isinstance(clone_url, str) else None,
            reference_comments=reference_comments,
        )

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "instance_id": self.instance_id,
            "repo": self.repo,
            "base_commit": self.base_commit,
            "head_commit": self.head_commit,
            "reference_comments": [comment.to_dict() for comment in self.reference_comments],
        }
        if self.clone_url:
            payload["clone_url"] = self.clone_url
        return payload


def _require_non_empty_str(value: Any, where: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{where}: must be a non-empty string")


def _validate_optional_line(value: Any, where: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError(f"{where}: line number must be an integer or null, got {value!r}")
    if value <= 0:
        raise SchemaError(f"{where}: line number must be a positive integer, got {value}")
    return value


def _normalize_line_range(
    start_line: Optional[int], end_line: Optional[int]
) -> tuple[Optional[int], Optional[int]]:
    if start_line is None and end_line is None:
        return None, None
    if start_line is None:
        start_line = end_line
    if end_line is None:
        end_line = start_line
    if start_line > end_line:
        start_line, end_line = end_line, start_line
    return start_line, end_line


def load_instances(dataset_path: Path, limit: Optional[int] = None) -> List[ReviewInstance]:
    """Load a standard JSONL dataset, validate each line, and return ReviewInstances.

    Invalid lines raise SchemaError with line numbers so bad data is caught early.
    Duplicate instance_id values are also rejected to prevent result-file overwrites.
    """
    dataset_path = Path(dataset_path)
    if not dataset_path.exists():
        raise SchemaError(f"Dataset does not exist: {dataset_path}")

    instances: List[ReviewInstance] = []
    seen_ids: set[str] = set()

    with dataset_path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            where = f"{dataset_path.name}:L{line_number}"
            try:
                data = json.loads(stripped)
            except json.JSONDecodeError as error:
                raise SchemaError(f"{where}: failed to parse JSON: {error}") from error

            instance = ReviewInstance.from_dict(data, where)
            if instance.instance_id in seen_ids:
                raise SchemaError(f"{where}: duplicate instance_id: {instance.instance_id}")
            seen_ids.add(instance.instance_id)
            instances.append(instance)

            if limit is not None and len(instances) >= limit:
                break

    return instances
