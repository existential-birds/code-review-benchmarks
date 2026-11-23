"""Check a candidate's Daydream commit, then run Daydream's gate, before it is scored.

``python -m loop.guard <daydream-worktree> <base> <commit>`` prints JSON for the
ledger and exits 1 on a refusal. The commit must be the only one on top of the base
and small; leave the gate, the coverage floor, tool settings, dependencies, the
vendored Harbor export and agent guidance alone; keep every test; and carry no
benchmark knowledge. Then ``make check`` must pass in the worktree, with a private
HOME, within ``gate_timeout_seconds``. The guard only reads the repository and never
commits, so it neither passes ``--no-verify`` nor touches the hooks.
"""
from __future__ import annotations

import argparse
import contextlib
import fnmatch
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import subprocess
import sys
import tempfile
import time

from benchmarks.aacr import convert as aacr
from benchmarks.aacr.upstream.converters.aacr_bench import parse_repo_from_pr_url
from benchmarks.reviewbench import convert as reviewbench
from loop import settings

ROOT = Path(__file__).resolve().parents[1]
REVIEWBENCH, AACR = ROOT / '.cache/src/ReviewBench', ROOT / '.cache/src/aacr-bench'
GIT = 'git'
GATE = ('make', 'check')
# Paths from the repository root. Make reads GNUmakefile or makefile before Makefile,
# and `make check` runs the two scripts named here.
PROTECTED = ('Makefile', 'GNUmakefile', 'makefile', 'pyproject.toml', 'uv.lock', '.python-version',
             'scripts/hooks/*', 'scripts/ci_*', 'scripts/check-naming.sh', 'scripts/check-packaged-runtimes.sh',
             '.github/*', 'docs/coverage.md', 'daydream/benchmarks/*', '.agents/*', 'AGENTS.md', 'CONTRIBUTING.md')
# Files that take precedence over pyproject.toml's tool settings, wherever they sit.
SETTINGS_FILES = {'pyproject.toml', 'uv.toml', 'ruff.toml', '.ruff.toml', 'mypy.ini', '.mypy.ini',
                  'pytest.ini', '.pytest.ini', 'tox.ini', 'setup.cfg', '.coveragerc'}
KEYWORDS = re.compile('reviewbench|aacr|harbor', re.IGNORECASE)
TEST_FUNCTION = re.compile(r'^\s*(?:async\s+)?def\s+test_', re.MULTILINE)
# Ways to skip, expect failure or deselect in pytest and unittest. The bare word
# "skip" is common in Daydream's tests, so only its call or attribute forms count.
ESCAPE = re.compile(r'\.skip(?:if|unless|test)?\b|\bskip(?:if)?\(|xfail|importorskip|skiptest|expectedfailure'
                    r'|deselect|collect_ignore|pytest_collection_modifyitems|pytest_ignore_collect', re.IGNORECASE)
HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)')
DIFF = ('--no-renames', '--no-ext-diff', '--no-textconv', '--no-color')


def git(worktree: Path, *arguments: str) -> str:
    result = subprocess.run([GIT, '-C', str(worktree), *arguments], capture_output=True,
                            encoding='utf-8', errors='surrogateescape')
    if result.returncode:
        raise RuntimeError(f'git {" ".join(arguments)} failed: {result.stderr.strip()}')
    return result.stdout


@contextlib.contextmanager
def _stdout_to_stderr():
    """Keep a child's progress off stdout, which carries the JSON."""
    sys.stdout.flush()
    saved = os.dup(1)
    os.dup2(2, 1)
    try:
        yield
    finally:
        os.dup2(saved, 1)
        os.close(saved)


def benchmark_names(reviewbench_source: Path = REVIEWBENCH, aacr_source: Path = AACR) -> set[str]:
    """Every review case's task name and its repository's owner/name, from both pinned sources."""
    source = Path(reviewbench_source).resolve()
    reviewbench.check_source(source)
    names = set()
    with tempfile.TemporaryDirectory(prefix='loop-guard-') as temporary, _stdout_to_stderr():
        reviewbench.export(source, Path(temporary) / 'full', 'full')
        for task in (Path(temporary) / 'full').iterdir():
            if task.is_dir():
                pr = json.loads((task / 'environment/pr.json').read_text())
                names |= {task.name, parse_repo_from_pr_url(pr['pr_url'])}
    for record in aacr.load_records(aacr_source):
        names |= {aacr.task_name(record), parse_repo_from_pr_url(record['githubPrUrl'])}
    if None in names:
        raise ValueError('A review case has no GitHub repository')
    return names


def name_pattern(names: set[str]) -> re.Pattern:
    """Whole names in any letter case, so a short name never matches inside a longer one.
    A full stop or ``.git`` may follow, but not a longer dotted name such as ``next.js``."""
    alternatives = '|'.join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    return re.compile(rf'(?<![\w.-])(?:{alternatives})(?![\w-]|\.(?!git\b)\w)', re.IGNORECASE)


def protected(path: str) -> bool:
    return PurePosixPath(path).name in SETTINGS_FILES or any(fnmatch.fnmatchcase(path, rule) for rule in PROTECTED)


def is_test_file(path: str) -> bool:
    name = PurePosixPath(path)
    return name.parts[0] == 'tests' and (fnmatch.fnmatchcase(name.name, 'test_*.py') or name.name.endswith('_test.py'))


def is_test_code(path: str) -> bool:
    name = PurePosixPath(path)
    return name.suffix == '.py' and (name.parts[0] == 'tests' or name.name == 'conftest.py')


def added_lines(worktree: Path, base: str, commit: str, path: str):
    """The new file's line number and text of each line the commit adds to path."""
    number = None
    for line in git(worktree, 'diff', '-U0', *DIFF, base, commit, '--', f':(literal){path}').splitlines():
        if hunk := HUNK.match(line):
            number = int(hunk[1])
        elif number is not None and line.startswith('+'):
            yield number, line[1:]
            number += 1


def run_gate(worktree: Path, timeout: int) -> dict:
    """Run Daydream's gate with a throwaway HOME, killing all of it at the timeout."""
    with tempfile.TemporaryDirectory(prefix='loop-gate-home-', ignore_cleanup_errors=True) as home:
        started, timed_out = time.monotonic(), False
        process = subprocess.Popen(GATE, cwd=worktree, env=os.environ | {'HOME': home}, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8',
                                   errors='replace', start_new_session=True)
        try:
            output = process.communicate(timeout=timeout)[0]
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGKILL)
        if timed_out:
            output = process.communicate()[0]
        seconds = time.monotonic() - started
    return dict(command=' '.join(GATE), returncode=None if timed_out else process.returncode,
                timed_out=timed_out, seconds=round(seconds, 1), output_tail=output[-4000:])


def check(worktree: Path, base: str, commit: str, loaded: settings.Settings, names: set[str]) -> dict:
    """Refuse an unsafe or benchmark-specific commit; only then run the gate."""
    worktree = Path(worktree).resolve()
    base, commit = (git(worktree, 'rev-parse', '--verify', '--end-of-options', f'{revision}^{{commit}}').strip()
                    for revision in (base, commit))
    refusals = []

    def refuse(kind: str, detail: str) -> None:
        refusals.append(dict(check=kind, detail=detail))

    parents = git(worktree, 'rev-list', '--parents', '-n', '1', commit).split()[1:]
    if parents != [base]:
        refuse('shape', f'Expected one commit on top of {base}; its parents are {parents}')
    if git(worktree, 'rev-parse', 'HEAD').strip() != commit or git(worktree, 'status', '--porcelain'):
        refuse('worktree', 'The worktree must be clean and checked out at the commit, so the gate runs on it')
    fields = git(worktree, 'diff', '--name-status', '-z', *DIFF, base, commit).split('\0')[:-1]
    changes = dict(zip(fields[1::2], fields[::2]))
    counts = [record.split('\t', 2)[:2] for record in
              git(worktree, 'diff', '--numstat', '-z', *DIFF, base, commit).split('\0') if record]
    lines = sum(int(count) for pair in counts for count in pair if count != '-')
    if len(changes) > loaded['max_diff_files']:
        refuse('shape', f'{len(changes)} files changed; at most {loaded["max_diff_files"]} may change')
    if lines > loaded['max_diff_lines']:
        refuse('shape', f'{lines} lines changed; at most {loaded["max_diff_lines"]} may change')
    pattern = name_pattern(names)
    for path, status in sorted(changes.items()):
        if protected(path):
            refuse('paths', f'{path} may not change')
        if is_test_file(path) and status == 'D':
            refuse('tests', f'{path} is deleted')
        elif is_test_file(path) and status != 'A':
            before, after = (len(TEST_FUNCTION.findall(git(worktree, 'show', f'{revision}:{path}')))
                             for revision in (base, commit))
            if after < before:
                refuse('tests', f'{path} has {after} test functions, fewer than its {before}')
        for number, line in added_lines(worktree, base, commit, path):
            if keyword := KEYWORDS.search(line):
                refuse('benchmark', f'{path}:{number} adds {keyword[0]!r}')
            # The name stays out of the ledger, which the proposer may read.
            if pattern.search(line):
                refuse('benchmark', f'{path}:{number} names a review case or its repository')
            if is_test_code(path) and ESCAPE.search(line):
                refuse('tests', f'{path}:{number} adds a skip, xfail or deselection')
    gate = None
    if not refusals:
        gate = run_gate(worktree, loaded['gate_timeout_seconds'])
        if gate['timed_out']:
            refuse('gate', f'make check ran past {loaded["gate_timeout_seconds"]} seconds')
        elif gate['returncode']:
            refuse('gate', f'make check exited {gate["returncode"]}')
    return dict(accepted=not refusals, worktree=str(worktree), base=base, commit=commit,
                files=len(changes), lines=lines, refusals=refusals, gate=gate)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('worktree', type=Path, help='Daydream worktree checked out at the commit')
    parser.add_argument('base', help='The Daydream commit the candidate builds on')
    parser.add_argument('commit', help="The proposer's commit")
    parser.add_argument('--reviewbench', type=Path, default=REVIEWBENCH, help='Clean checkout of the pinned ReviewBench')
    parser.add_argument('--aacr', type=Path, default=AACR, help='Clean checkout of the pinned AACR-Bench')
    arguments = parser.parse_args(argv)
    names = benchmark_names(arguments.reviewbench, arguments.aacr)
    result = check(arguments.worktree, arguments.base, arguments.commit, settings.load(), names)
    print(json.dumps(result, indent=2))
    return 0 if result['accepted'] else 1


if __name__ == '__main__':
    sys.exit(main())
