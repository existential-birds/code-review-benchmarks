"""Unmodified pinned AACR modules, whose imports use a flat module namespace."""

import importlib
import sys
from pathlib import Path

# AACR imports config/schema/judge by their original top-level names. Alias the
# package names to those same objects so the transport is installed once.
sys.path.insert(0, str(Path(__file__).resolve().parent))
for _name in ("config", "schema", "repo_utils", "judge", "evaluate"):
    _module = importlib.import_module(_name)
    globals()[_name] = _module
    sys.modules[f"{__name__}.{_name}"] = _module
