"""AACR-Bench's evaluator, modified; see docs/citing.md for the changes.

Copied from alibaba/aacr-bench ``evaluation/`` at
ee9d7fef70ef31acea2ff7ae316ce0a9575fe4df under Apache-2.0. AACR imports its
modules by top-level name (``import judge``), so this package registers them under
both names. Each name then refers to the same module, and patching one, such as the
judge client, patches both.
"""

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
for _name in ("config", "schema", "repo_utils", "judge", "evaluate"):
    _module = importlib.import_module(_name)
    globals()[_name] = _module
    sys.modules[f"{__name__}.{_name}"] = _module
