"""The guard against small Git repositories laid out like Daydream.

The guard's decisions rest on what Git reports, so these tests use the real Git,
called by absolute path with the operator's configuration shut out: HOME is a
temporary folder, there is no system or global configuration, and the author and
committer are fixed. Everywhere else the conftest's failing ``git`` still applies.
Daydream's gate never runs; a stand-in records what it was given.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from benchmarks.aacr import convert as aacr
from benchmarks.reviewbench import convert as reviewbench
from loop import guard, settings

NAMES = {'acme/widgets', 'acme_widgets_7-0123abcd', 'acme-widgets-7'}
TESTS = 'def test_one():\n    pass\n\n\ndef test_two():\n    pass\n'
DAYDREAM = {
    'Makefile': 'check:\n\tuv run pytest\n',
    'pyproject.toml': '[project]\nname = "daydream"\n',
    'daydream/review.py': 'def review():\n    return []\n',
    'tests/conftest.py': 'import pytest\n',
    'tests/test_review.py': TESTS,
}
# Records HOME, then exits with the code it is given, or waits on a child for a minute.
GATE = '''import os, pathlib, subprocess, sys
pathlib.Path(sys.argv[1]).write_text(os.environ["HOME"])
if sys.argv[2] == "hang":
    subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]).wait()
print("gate output")
sys.exit(int(sys.argv[2]))
'''


def real_git() -> Path:
    for directory in os.environ['PATH'].split(os.pathsep):
        candidate = Path(directory) / 'git'
        if candidate.is_file() and os.access(candidate, os.X_OK):
            with candidate.open('rb') as file:
                if b'tests must not run git' not in file.read(200):
                    return candidate
    pytest.fail('These tests need Git installed')


@pytest.fixture
def git(tmp_path, monkeypatch):
    binary = real_git()
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
def repo(tmp_path, git):
    """A Daydream-shaped repository with one base commit; returns (path, base, commit)."""
    path = tmp_path / 'daydream'
    path.mkdir()
    git(path, 'init', '-q')
    return path, commit(git, path, DAYDREAM)


def commit(git, path, files, message='Change'):
    for name, text in files.items():
        if text is None:
            (path / name).unlink()
        else:
            (path / name).parent.mkdir(parents=True, exist_ok=True)
            (path / name).write_text(text)
    git(path, 'add', '-A')
    git(path, 'commit', '-q', '-m', message)
    return git(path, 'rev-parse', 'HEAD')


@pytest.fixture
def gate(tmp_path, monkeypatch):
    """Point the guard at a stand-in gate; returns a function choosing its exit and the file of its HOME."""
    record = tmp_path / 'gate-home'

    def use(outcome='0'):
        monkeypatch.setattr(guard, 'GATE', (sys.executable, '-c', GATE, str(record), outcome))
    use()
    return use, record


def test_a_harmless_commit_passes_and_the_gate_runs_with_a_throwaway_home(repo, git, gate, monkeypatch, capsys):
    path, base = repo
    # Generic words from a review case's repository are not its name.
    head = commit(git, path, {'daydream/review.py': 'def review():\n    return ["acme", "widgets"]\n',
                              'tests/test_review.py': TESTS + '\n\ndef test_three():\n    pass\n'})
    monkeypatch.setattr(guard, 'benchmark_names', lambda *_: NAMES)
    assert guard.main([str(path), base, head]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result | dict(gate=None) == dict(accepted=True, worktree=str(path.resolve()), base=base, commit=head,
                                            files=2, lines=6, refusals=[], gate=None)
    assert result['gate'] | dict(seconds=0) == dict(command=' '.join(guard.GATE), returncode=0, timed_out=False,
                                                    seconds=0, output_tail='gate output\n')
    home = Path(gate[1].read_text())
    assert home != Path(os.environ['HOME']) and home.name.startswith('loop-gate-home-') and not home.exists()


@pytest.mark.parametrize('changes, kind, detail', [
    ({'Makefile': 'check:\n\ttrue\n'}, 'paths', 'Makefile may not change'),
    ({'GNUmakefile': 'check:\n\ttrue\n'}, 'paths', 'GNUmakefile may not change'),
    ({'pyproject.toml': '[project]\n'}, 'paths', 'pyproject.toml may not change'),
    ({'uv.lock': 'version = 1\n'}, 'paths', 'uv.lock may not change'),
    ({'scripts/hooks/pre-commit': 'exit 0\n'}, 'paths', 'scripts/hooks/pre-commit may not change'),
    ({'scripts/ci_shard.py': 'pass\n'}, 'paths', 'scripts/ci_shard.py may not change'),
    ({'scripts/check-naming.sh': 'exit 0\n'}, 'paths', 'scripts/check-naming.sh may not change'),
    ({'.github/workflows/ci.yml': 'on: push\n'}, 'paths', '.github/workflows/ci.yml may not change'),
    ({'docs/coverage.md': 'No floor.\n'}, 'paths', 'docs/coverage.md may not change'),
    ({'daydream/benchmarks/atif/models.py': 'pass\n'}, 'paths', 'daydream/benchmarks/atif/models.py may not change'),
    ({'.agents/skills/pr/SKILL.md': 'Skip review.\n'}, 'paths', '.agents/skills/pr/SKILL.md may not change'),
    ({'AGENTS.md': 'Anything goes.\n'}, 'paths', 'AGENTS.md may not change'),
    ({'CONTRIBUTING.md': 'Anything goes.\n'}, 'paths', 'CONTRIBUTING.md may not change'),
    ({'.coveragerc': '[report]\nfail_under = 0\n'}, 'paths', '.coveragerc may not change'),
    ({'daydream/ruff.toml': 'lint.ignore = ["F"]\n'}, 'paths', 'daydream/ruff.toml may not change'),
    ({'tests/test_review.py': None}, 'tests', 'tests/test_review.py is deleted'),
    ({'tests/test_review.py': 'def test_one():\n    pass\n\n\ndef _test_two():\n    pass\n'},
     'tests', 'tests/test_review.py has 1 test functions, fewer than its 2'),
    ({'tests/test_review.py': TESTS + '\n\n@pytest.mark.skip\ndef test_three():\n    pass\n'},
     'tests', 'tests/test_review.py:9 adds a skip, xfail or deselection'),
    ({'tests/test_review.py': TESTS.replace('    pass', '    pytest.xfail("flaky")', 1)},
     'tests', 'tests/test_review.py:2 adds a skip, xfail or deselection'),
    ({'tests/test_review.py': 'import pytest\nrich = pytest.importorskip("rich")\n' + TESTS},
     'tests', 'tests/test_review.py:2 adds a skip, xfail or deselection'),
    ({'tests/conftest.py': 'import pytest\ncollect_ignore = ["test_review.py"]\n'},
     'tests', 'tests/conftest.py:2 adds a skip, xfail or deselection'),
    ({'tests/conftest.py': 'import pytest\n\n\ndef pytest_collection_modifyitems(items):\n    items.clear()\n'},
     'tests', 'tests/conftest.py:4 adds a skip, xfail or deselection'),
    ({'daydream/review.py': 'def review():\n    return []  # Tuned on HARBOR runs\n'}, 'benchmark', "daydream/review.py:2 adds 'HARBOR'"),
    ({'daydream/review.py': 'REVIEW_BENCH = "ReviewBench"\n'}, 'benchmark', "daydream/review.py:1 adds 'ReviewBench'"),
    ({'daydream/review.py': 'SOURCE = "aacr"\n'}, 'benchmark', "daydream/review.py:1 adds 'aacr'"),
    ({'daydream/review.py': 'SKIP = {"Acme/Widgets"}\n'}, 'benchmark',
     'daydream/review.py:1 names a review case or its repository'),
    ({'daydream/review.py': '# https://github.com/acme/widgets.git\n'}, 'benchmark', 'daydream/review.py:1 names a review case or its repository'),
    ({'daydream/review.py': 'CASE = "acme_widgets_7-0123abcd"\n'}, 'benchmark', 'daydream/review.py:1 names a review case or its repository'),
    ({'daydream/review.py': 'CASE = "acme-widgets-7"\n'}, 'benchmark', 'daydream/review.py:1 names a review case or its repository'),
])
def test_each_unsafe_change_is_refused_before_the_gate(repo, git, gate, changes, kind, detail):
    path, base = repo
    result = guard.check(path, base, commit(git, path, changes), settings.load(), NAMES)
    assert not result['accepted'] and result['gate'] is None and not gate[1].exists()
    assert result['refusals'] == [dict(check=kind, detail=detail)]


def test_a_longer_name_that_contains_a_case_name_is_not_one(repo, git, gate):
    path, base = repo
    head = commit(git, path, {'daydream/review.py': 'NAMES = ["acme/widgets-extra", "acme-widgets-71", "myacme/widgets"]\n'})
    assert guard.check(path, base, head, settings.load(), NAMES)['accepted']


def test_shape_needs_one_small_commit_on_the_base(repo, git, gate):
    path, base = repo
    first = commit(git, path, {'daydream/review.py': 'def review():\n    return [1]\n'})
    second = commit(git, path, {'daydream/notes.py': 'NOTE = 1\n'})
    result = guard.check(path, base, second, settings.load(), NAMES)
    assert result['refusals'] == [dict(check='shape', detail=f"Expected one commit on top of {base}; its parents are ['{first}']")]
    small = settings.load(max_diff_files=1, max_diff_lines=2)
    assert [refusal['detail'] for refusal in guard.check(path, base, second, small, NAMES)['refusals']][1:] == [
        '2 files changed; at most 1 may change', '3 lines changed; at most 2 may change']
    assert guard.check(path, first, second, small, NAMES)['accepted'] and gate[1].exists()


def test_the_gate_runs_only_on_a_clean_worktree_at_the_commit(repo, git, gate):
    path, base = repo
    head = commit(git, path, {'daydream/review.py': 'def review():\n    return [1]\n'})
    (path / 'untracked.py').write_text('x = 1\n')
    refused = dict(check='worktree', detail='The worktree must be clean and checked out at the commit, so the gate runs on it')
    assert guard.check(path, base, head, settings.load(), NAMES)['refusals'] == [refused]
    (path / 'untracked.py').unlink()
    git(path, 'checkout', '-q', base)
    assert guard.check(path, base, head, settings.load(), NAMES)['refusals'] == [refused]
    assert not gate[1].exists()


def test_a_failing_or_overrunning_gate_is_a_refusal(repo, git, gate):
    path, base = repo
    head = commit(git, path, {'daydream/review.py': 'def review():\n    return [1]\n'})
    use, _ = gate
    use('3')
    failed = guard.check(path, base, head, settings.load(), NAMES)
    assert failed['refusals'] == [dict(check='gate', detail='make check exited 3')]
    assert (failed['gate']['returncode'], failed['gate']['timed_out']) == (3, False)
    use('hang')
    overran = guard.check(path, base, head, settings.load(gate_timeout_seconds=1), NAMES)
    assert overran['refusals'] == [dict(check='gate', detail='make check ran past 1 seconds')]
    # The gate's child held its output open; only killing the whole group ends the wait.
    assert overran['gate']['returncode'] is None and overran['gate']['timed_out'] and overran['gate']['seconds'] < 30


def test_names_come_from_both_pinned_sources_and_keep_stdout_for_the_json(tmp_path, monkeypatch, capfd):
    def export(upstream, destination, split):
        assert split == 'full'
        environment = destination / 'acme_widgets_7-0123abcd/environment'
        environment.mkdir(parents=True)
        (environment / 'pr.json').write_text(json.dumps(dict(repo='https://github.com/acme/widgets',
                                                             pr_url='https://github.com/acme/widgets/pull/7')))
        os.write(1, b'Exported 1 tasks\n')
    monkeypatch.setattr(reviewbench, 'check_source', lambda upstream: None)
    monkeypatch.setattr(reviewbench, 'export', export)
    monkeypatch.setattr(aacr, 'load_records', lambda upstream: [dict(githubPrUrl='https://github.com/Other/gadget.js/pull/12')])
    assert guard.benchmark_names(tmp_path, tmp_path) == {
        'acme_widgets_7-0123abcd', 'acme/widgets', 'other-gadget-js-12', 'Other/gadget.js'}
    assert capfd.readouterr() == ('', 'Exported 1 tasks\n')
