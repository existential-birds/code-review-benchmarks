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
