"""Tests never run Git through PATH. A ``git`` that always fails comes first on PATH,
so any call that slips past the fakes in ``fake_git.py`` fails the test instead of
reaching the real Git and the operator's configuration. The exceptions are checks that
depend on what Git itself reports: the ``git`` fixture, for the guard's and the proposer
step's tests, calls the real binary by absolute path with the operator's configuration
shut out, and ``real_git`` hands the binary to the frozen-comparison test, which runs
Daydream's own Git reads on a real checkout."""
import os
import subprocess

import pytest

from real_git import real_git as find_real_git


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
    return find_real_git()


@pytest.fixture
def git(tmp_path, monkeypatch):
    """The real Git, for the loop's own Git calls too, with a temporary HOME, no system or
    global configuration, no other GIT_ variables, and a fixed author and committer.
    Returns a function running it in a repository."""
    from loop import guard
    binary = find_real_git()
    for name in [name for name in os.environ if name.startswith('GIT_')]:
        monkeypatch.delenv(name)
    (tmp_path / 'home').mkdir()
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    for name, value in dict(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                            GIT_AUTHOR_NAME='Proposer', GIT_AUTHOR_EMAIL='proposer@example.com',
                            GIT_COMMITTER_NAME='Proposer', GIT_COMMITTER_EMAIL='proposer@example.com',
                            GIT_AUTHOR_DATE='2026-01-01T00:00:00Z', GIT_COMMITTER_DATE='2026-01-01T00:00:00Z').items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(guard, 'GIT', str(binary))

    def run(repo, *arguments):
        return subprocess.run([str(binary), '-C', str(repo), *arguments], check=True,
                              capture_output=True, text=True).stdout.strip()
    return run


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
