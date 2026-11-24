"""The real Git for tests whose checks rest on what Git itself reports.

The conftest's ``git`` fixture finds it past the failing shim and shuts the operator's
configuration out; ``commit`` writes files and commits them with it.
"""
import os
from pathlib import Path

import pytest


def real_git() -> Path:
    for directory in os.environ['PATH'].split(os.pathsep):
        candidate = Path(directory) / 'git'
        if candidate.is_file() and os.access(candidate, os.X_OK):
            with candidate.open('rb') as file:
                if b'tests must not run git' not in file.read(200):
                    return candidate
    pytest.fail('These tests need Git installed')


def commit(git, path, files, *options):
    for name, content in files.items():
        if content is None:
            (path / name).unlink()
        else:
            (path / name).parent.mkdir(parents=True, exist_ok=True)
            (path / name).write_bytes(content if isinstance(content, bytes) else content.encode())
    git(path, 'add', '-A')
    git(path, 'commit', '-q', '-m', 'Change', *options)
    return git(path, 'rev-parse', 'HEAD')
