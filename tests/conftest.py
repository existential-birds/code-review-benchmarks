"""Tests never run Git. A ``git`` that always fails comes first on PATH, so any
call that slips past the fakes in ``fake_git.py`` fails the test instead of
reaching the real Git and the operator's configuration."""
import os

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
