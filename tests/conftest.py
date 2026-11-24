"""Tests never run Git through PATH. A ``git`` that always fails comes first on PATH,
so any call that slips past the fakes in ``fake_git.py`` fails the test instead of
reaching the real Git and the operator's configuration. The exceptions are checks that
depend on what Git itself reports, which ask for ``real_git``: ``test_guard.py`` calls it
by absolute path with the operator's configuration shut out, and the frozen-comparison
test runs Daydream's own Git reads on a real checkout."""
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True, scope='session')
def no_real_git(tmp_path_factory):
    shim = tmp_path_factory.mktemp('no-git')
    git = shim / 'git'
    git.write_text('#!/bin/sh\necho "tests must not run git: git $*" >&2\nexit 97\n')
    git.chmod(0o755)
    previous = os.environ['PATH']
    os.environ['PATH'] = f'{shim}{os.pathsep}{previous}'
    yield
    os.environ['PATH'] = previous


@pytest.fixture
def real_git():
    """The installed Git binary, past the failing one."""
    for directory in os.environ['PATH'].split(os.pathsep):
        candidate = Path(directory) / 'git'
        if candidate.is_file() and os.access(candidate, os.X_OK):
            with candidate.open('rb') as file:
                if b'tests must not run git' not in file.read(200):
                    return candidate
    pytest.fail('These tests need Git installed')


@pytest.fixture
def pinned_wheel(tmp_path, monkeypatch):
    """A stand-in Daydream build in a private cache, pinned as if by build.json."""
    import hashlib
    import zipfile
    from benchmarks.daydream import build
    cache = tmp_path / 'daydream-cache'
    cache.mkdir()
    wheel = cache / 'daydream-0.33.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('daydream-0.33.0.dist-info/METADATA', 'Name: daydream\nVersion: 0.33.0\n')
    (cache / build.LOCK_NAME).write_text('pinned-dependency==1.0 --hash=sha256:' + '0' * 64 + '\n')
    monkeypatch.setattr(build, 'CACHE', cache)
    monkeypatch.setitem(build.BUILD, 'wheel_name', wheel.name)
    monkeypatch.setitem(build.BUILD, 'wheel_sha256', hashlib.sha256(wheel.read_bytes()).hexdigest())
    return wheel


@pytest.fixture
def candidate_build(tmp_path):
    """A stand-in candidate build beside its own build.json, as ``build --candidate`` leaves it."""
    import hashlib
    import json
    import zipfile
    from benchmarks.daydream import build
    folder = tmp_path / 'daydream-candidates' / ('c' * 40)
    folder.mkdir(parents=True)
    wheel = folder / 'daydream-0.34.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('daydream-0.34.0.dist-info/METADATA', 'Name: daydream\nVersion: 0.34.0\nSummary: candidate\n')
    (folder / build.LOCK_NAME).write_text('candidate-dependency==1.0 --hash=sha256:' + '1' * 64 + '\n')
    build_file = folder / 'build.json'
    build_file.write_text(json.dumps({
        'source_commit': 'c' * 40, 'version': '0.34.0', 'wheel_name': wheel.name,
        'wheel_sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(),
        'codex_cli': build.BUILD['codex_cli'], 'candidate': True}))
    return build_file
