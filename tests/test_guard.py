"""The guard against small Git repositories laid out like Daydream.

The guard's decisions rest on what Git reports, so these tests use the real Git,
called by absolute path through the conftest's ``git`` fixture, with the operator's
configuration shut out. Everywhere else the conftest's failing ``git`` still applies.
Daydream's gate never runs; a stand-in records what it was given.
"""
import json
import os
from pathlib import Path
import sys
import time

import pytest

from benchmarks.aacr import convert as aacr
from benchmarks.reviewbench import convert as reviewbench
from loop import guard, settings
from real_git import commit

NAMES = {'acme/widgets', 'acme_widgets_7-0123abcd', 'acme-widgets-7'}
TESTS = 'def test_one():\n    pass\n\n\ndef test_two():\n    pass\n'
DAYDREAM = {
    'Makefile': 'check:\n\tuv run pytest\n',
    'pyproject.toml': '[project]\nname = "daydream"\n',
    'daydream/review.py': 'def review():\n    return []\n',
    'tests/conftest.py': 'import pytest\n',
    'tests/test_review.py': TESTS,
}
UV = {'UV_CACHE_DIR': '/operator/uv-cache', 'UV_PYTHON_INSTALL_DIR': '/operator/uv-python'}
# Records what it was given, then exits with the code it is given. "hang" waits for a
# minute and "linger" exits 0; both first start a child that would leave a mark late.
GATE = '''import json, os, pathlib, subprocess, sys
record, outcome = pathlib.Path(sys.argv[1]), sys.argv[2]
record.write_text(json.dumps(dict(environment=dict(os.environ), directory=os.getcwd(),
                                  files=sorted(os.listdir()), review=pathlib.Path("daydream/review.py").read_text())))
if outcome in ("hang", "linger"):
    subprocess.Popen([sys.executable, "-c", "import pathlib, sys, time; time.sleep(1.5); pathlib.Path(sys.argv[1]).touch()",
                      str(record) + ".late"])
if outcome == "hang":
    __import__("time").sleep(60)
print("gate output")
sys.exit(0 if outcome == "linger" else int(outcome))
'''


@pytest.fixture
def repo(tmp_path, git):
    """A Daydream-shaped repository with one base commit; returns (path, base)."""
    path = tmp_path / 'daydream'
    path.mkdir()
    git(path, 'init', '-q')
    return path, commit(git, path, DAYDREAM)


@pytest.fixture
def gate(tmp_path, monkeypatch):
    """Point the guard at a stand-in gate; returns a function choosing its outcome, and its record."""
    record = tmp_path / 'gate.json'
    monkeypatch.setattr(guard, 'uv_directories', lambda: dict(UV))

    def use(outcome='0'):
        monkeypatch.setattr(guard, 'GATE', (sys.executable, '-c', GATE, str(record), outcome))
    use()
    return use, record


def test_a_harmless_commit_passes_and_the_gate_runs_in_a_throwaway_clone(repo, git, gate, monkeypatch, capsys):
    path, base = repo
    # Generic words from a review case's repository are not its name.
    head = commit(git, path, {'daydream/review.py': 'def review():\n    return ["acme", "widgets"]\n',
                              'tests/test_review.py': TESTS + '\n\ndef test_three():\n    pass\n'})
    monkeypatch.setattr(guard, 'benchmark_names', lambda *_: NAMES)
    monkeypatch.setenv('MAKEFLAGS', '-k')
    monkeypatch.setenv('PYTEST_ADDOPTS', '-k nothing')
    assert guard.main([str(path), base, head]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result | dict(gate=None) == dict(accepted=True, worktree=str(path.resolve()), base=base, commit=head,
                                            files=2, lines=6, refusals=[], gate=None)
    assert result['gate'] | dict(seconds=0) == dict(command=' '.join(guard.GATE), returncode=0, timed_out=False,
                                                    seconds=0, output_tail='gate output\n')
    seen = json.loads(gate[1].read_text())
    seen['environment'].pop('LC_CTYPE', None)  # Python's own locale coercion in the stand-in
    seen['environment'].pop('__CF_USER_TEXT_ENCODING', None)  # macOS adds it to every process
    home, directory = Path(seen['environment']['HOME']), Path(seen['directory'])
    assert seen['environment'] == {name: os.environ[name] for name in guard.GATE_ENVIRONMENT if name in os.environ} | UV | {
        'UV_OFFLINE': '1', 'HOME': str(home)}
    # Resolved: on macOS the temporary folder sits under /var, a link to /private/var, and the stand-in's
    # working directory comes back resolved.
    assert home.name == 'home' and home.parent.resolve() == directory.parent.resolve() and not home.parent.exists()
    assert seen['review'] == 'def review():\n    return ["acme", "widgets"]\n'


def test_nothing_in_the_worktree_but_its_commits_reaches_the_checks_or_the_gate(repo, git, gate):
    path, base = repo
    head = commit(git, path, {'.gitignore': 'pytest.ini\n', 'daydream/review.py': 'def review():\n    return [1]\n'})
    git(path, 'checkout', '-q', base)
    (path / 'pytest.ini').write_text('[pytest]\naddopts = -k nothing\n')
    (path / 'untracked.py').write_text('x = 1\n')
    assert guard.check(path, base, head, settings.load(), NAMES)['accepted']
    seen = json.loads(gate[1].read_text())
    assert seen['files'] == ['.git', '.gitignore', 'Makefile', 'daydream', 'pyproject.toml', 'tests']
    assert seen['review'] == 'def review():\n    return [1]\n' and Path(seen['directory']) != path
    # The worktree's own attributes can't hide what the commit adds.
    (path / 'pytest.ini').unlink()
    (path / 'untracked.py').unlink()
    (path / '.git/info/attributes').write_text('* -diff\n')
    named = commit(git, path, {'daydream/review.py': 'SKIP = {"acme/widgets"}\n'})
    assert guard.check(path, base, named, settings.load(), NAMES)['refusals'] == [
        dict(check='benchmark', detail='daydream/review.py:1 names a review case or its repository')]


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
    ({'pytest.toml': '[pytest]\naddopts = ["-k", "nothing"]\n'}, 'paths', 'pytest.toml may not change'),
    ({'daydream/ruff.toml': 'lint.ignore = ["F"]\n'}, 'paths', 'daydream/ruff.toml may not change'),
    ({'docs/.gitattributes': '*.md text\n'}, 'paths', 'docs/.gitattributes may not change'),
    ({'.lfsconfig': '[lfs]\n\turl = https://example.invalid\n'}, 'paths', '.lfsconfig may not change'),
    # Casefolded, for a disk that ignores case. Its content sets no diff attribute, since on such a disk
    # Git reads it as the folder's .gitattributes.
    ({'docs/.GitAttributes': '*.md text\n'}, 'paths', 'docs/.GitAttributes may not change'),
    ({'.gitmodules': '[submodule "x"]\n\tpath = x\n\turl = https://example.invalid/x\n'}, 'paths',
     '.gitmodules may not change'),
    ({'daydream/logo.png': b'\x89PNG\0\0'}, 'shape', 'daydream/logo.png is binary'),
    ({'tests/test_review.py': None}, 'tests', 'tests/test_review.py is deleted'),
    ({'tests/test_review.py': 'def test_one():\n    pass\n\n\ndef _test_two():\n    pass\n'},
     'tests', 'tests/test_review.py has 1 test functions, fewer than its 2'),
    ({'tests/test_review.py': TESTS.replace('test_two', 'test_one')},
     'tests', 'tests/test_review.py has 1 test functions, fewer than its 2'),
    ({'tests/test_review.py': TESTS + 'test_two = None\n'},
     'tests', 'tests/test_review.py has 1 test functions, fewer than its 2'),
    ({'tests/test_review.py': f'SOURCE = """\n{TESTS}"""\n'},
     'tests', 'tests/test_review.py has 0 test functions, fewer than its 2'),
    ({'tests/test_review.py': f'class TestReview:\n    pass\n\n\n{TESTS}'.replace('def test_one', 'def helper')},
     'tests', 'tests/test_review.py has 1 test functions, fewer than its 2'),
    ({'tests/test_review.py': TESTS + '\n\n@pytest.mark.skip\ndef test_three():\n    pass\n'},
     'tests', 'tests/test_review.py:9 adds a skip, xfail, deselection or pytest hook'),
    ({'tests/test_review.py': TESTS.replace('    pass', '    pytest.xfail("flaky")', 1)},
     'tests', 'tests/test_review.py:2 adds a skip, xfail, deselection or pytest hook'),
    ({'tests/test_review.py': 'import pytest\nrich = pytest.importorskip("rich")\n' + TESTS},
     'tests', 'tests/test_review.py:2 adds a skip, xfail, deselection or pytest hook'),
    ({'tests/test_review.py': '__test__ = False\n' + TESTS},
     'tests', 'tests/test_review.py:1 adds a skip, xfail, deselection or pytest hook'),
    ({'tests/test_review.py': 'from pytest import (\n    mark,\n    skip as s,\n)\n' + TESTS.replace('    pass', '    s("x")', 1)},
     'tests', 'tests/test_review.py imports a skip or xfail helper by name'),
    ({'tests/conftest.py': 'import pytest\ncollect_ignore = ["test_review.py"]\n'},
     'tests', 'tests/conftest.py:2 adds a skip, xfail, deselection or pytest hook'),
    ({'tests/conftest.py': 'import pytest\npytest_plugins = ["quiet"]\n'},
     'tests', 'tests/conftest.py:2 adds a skip, xfail, deselection or pytest hook'),
    ({'tests/conftest.py': 'import os\n\n\ndef pytest_sessionstart(session):\n    os._exit(0)\n'},
     'tests', 'tests/conftest.py:4 adds a skip, xfail, deselection or pytest hook'),
    ({'daydream/review.py': 'def review():\n    if False:  # pragma: no cover\n        return [1]\n    return []\n'},
     'tests', 'daydream/review.py:2 adds a coverage exclusion'),
    ({'daydream/review.py': 'def review():\n    return []  # Tuned on HARBOR runs\n'}, 'benchmark', "daydream/review.py:2 adds 'harbor'"),
    ({'daydream/review.py': 'REVIEW_BENCH = "ReviewBench"\nSOURCE = "reviewbench"\n'},
     'benchmark', "daydream/review.py:1 adds 'reviewbench' (and 1 more lines)"),
    ({'daydream/review.py': 'SOURCE = "aacr"\n'}, 'benchmark', "daydream/review.py:1 adds 'aacr'"),
    ({'daydream/review.py': 'SKIP = {"Acme/Widgets"}\n'}, 'benchmark',
     'daydream/review.py:1 names a review case or its repository'),
    ({'daydream/review.py': '# https://github.com/acme/widgets.git\n'}, 'benchmark',
     'daydream/review.py:1 names a review case or its repository'),
    ({'daydream/review.py': 'CASE = "acme_widgets_7-0123abcd"\n'}, 'benchmark',
     'daydream/review.py:1 names a review case or its repository'),
    ({'daydream/review.py': 'CASE = "acme-widgets-7"\n'}, 'benchmark',
     'daydream/review.py:1 names a review case or its repository'),
])
def test_each_unsafe_change_is_refused_before_the_gate(repo, git, gate, changes, kind, detail):
    path, base = repo
    result = guard.check(path, base, commit(git, path, changes), settings.load(), NAMES)
    assert not result['accepted'] and result['gate'] is None and not gate[1].exists()
    assert result['refusals'] == [dict(check=kind, detail=detail)]


@pytest.mark.parametrize('changes', [
    {'.gitattributes': '*.py -diff\n', 'daydream/review.py': 'SKIP = {"acme/widgets"}\n'},
    {'daydream/review.py': b'\0SKIP = {"acme/widgets"}\n'},
])
def test_content_marked_or_made_binary_is_still_read(repo, git, gate, changes):
    path, base = repo
    refusals = guard.check(path, base, commit(git, path, changes), settings.load(), NAMES)['refusals']
    assert dict(check='shape', detail='daydream/review.py is binary') in refusals
    assert dict(check='benchmark', detail='daydream/review.py:1 names a review case or its repository') in refusals


def test_ordinary_code_that_resembles_a_refusal_passes(repo, git, gate):
    path, base = repo
    head = commit(git, path, {
        'daydream/review.py': 'NAMES = ["acme/widgets-extra", "acme-widgets-71", "myacme/widgets"]\n',
        'tests/test_review.py': TESTS + '\n\ndef test_three():\n    args = None\n    assert args.skip is False  # skip tier\n'})
    assert guard.check(path, base, head, settings.load(), NAMES)['accepted']


def test_shape_needs_one_small_commit_on_the_base_that_changes_something(repo, git, gate):
    path, base = repo
    first = commit(git, path, {'daydream/review.py': 'def review():\n    return [1]\n'})
    second = commit(git, path, {'daydream/notes.py': 'NOTE = 1\n'})
    result = guard.check(path, base, second, settings.load(), NAMES)
    assert result['refusals'] == [dict(check='shape', detail=f"Expected one commit on top of {base}; its parents are ['{first}']")]
    small = settings.load(max_diff_files=1, max_diff_lines=2)
    assert [refusal['detail'] for refusal in guard.check(path, base, second, small, NAMES)['refusals']][1:] == [
        '2 files changed; at most 1 may change', '3 lines changed; at most 2 may change']
    empty = commit(git, path, {}, '--allow-empty')
    assert guard.check(path, second, empty, settings.load(), NAMES)['refusals'] == [
        dict(check='shape', detail='The commit changes no file')]
    assert guard.check(path, first, second, small, NAMES)['accepted'] and gate[1].exists()


def test_a_failing_or_overrunning_gate_is_a_refusal_and_leaves_nothing_running(repo, git, gate):
    path, base = repo
    head = commit(git, path, {'daydream/review.py': 'def review():\n    return [1]\n'})
    use, record = gate
    use('3')
    failed = guard.check(path, base, head, settings.load(), NAMES)
    assert failed['refusals'] == [dict(check='gate', detail='make check exited 3')]
    assert (failed['gate']['returncode'], failed['gate']['timed_out']) == (3, False)
    use('hang')
    overran = guard.check(path, base, head, settings.load(gate_timeout_seconds=1), NAMES)
    assert overran['refusals'] == [dict(check='gate', detail='make check ran past 1 seconds')]
    assert overran['gate']['returncode'] is None and overran['gate']['timed_out'] and overran['gate']['seconds'] < 30
    use('linger')
    assert guard.check(path, base, head, settings.load(), NAMES)['accepted']
    # Each gate's child would have left its mark 1.5 seconds in.
    time.sleep(2)
    assert not Path(f'{record}.late').exists()


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
    monkeypatch.setattr(aacr, 'load_records', lambda upstream: [])
    monkeypatch.setattr(reviewbench, 'export', lambda upstream, destination, split: destination.mkdir())
    with pytest.raises(ValueError, match='gave no review case names'):
        guard.benchmark_names(tmp_path, tmp_path)
    with pytest.raises(ValueError, match='No names to match'):
        guard.name_pattern(set())


def test_a_commit_the_guard_cannot_check_still_gives_json(repo, git, monkeypatch, capsys):
    path, base = repo
    monkeypatch.setattr(guard, 'benchmark_names', lambda *_: NAMES)
    assert guard.main([str(path), base, 'f' * 40]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['accepted'] is False and result['error']
    def unpinned(*_):
        raise ValueError('Expected ReviewBench revision x')
    monkeypatch.setattr(guard, 'benchmark_names', unpinned)
    assert guard.main([str(path), base, base]) == 2
    assert json.loads(capsys.readouterr().out) == dict(accepted=False, error='Expected ReviewBench revision x')
