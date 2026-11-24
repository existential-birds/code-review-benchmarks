"""Check a candidate's Daydream commit, then run Daydream's gate, before it is scored.

``python -m loop.guard <daydream-worktree> <base> <commit>`` prints JSON for the
ledger and exits 1 on a refusal, or 2 with an ``error`` when it couldn't check the
commit (an unknown revision, a missing or unpinned benchmark source). It clones the worktree's repository at the commit
into a folder of its own, so nothing untracked, ignored or configured in the worktree
reaches the checks or the gate. The commit must be the only one on top of the base,
change at least one file and stay small, with no binary files; leave the gate, the
coverage floor, tool settings, ``.gitattributes``, dependencies, the vendored Harbor
export and agent guidance alone; keep every test, add no skip, xfail, deselection,
pytest hook or coverage exclusion; and carry no benchmark knowledge. Then ``make
check`` must pass in the clone within ``gate_timeout_seconds``, with a throwaway HOME
and only an allowlisted environment. On macOS it deselects ``gate_deselect_macos``, the
pinned build's own tests that fail there before any change. uv runs offline from the operator's cache, so
one ordinary ``make check`` in a Daydream checkout must have filled it first. The
guard never commits; the Git it runs has hooks off, so nothing a candidate holds runs outside its gate.

It does not catch gutted test bodies or assertions, code that behaves differently
under test, or names written in disguise. It matches only a review case's name and
its repository's full ``owner/name``, never a bare repository name, and it refuses
any added line that mentions harbor, even one that already did before the change.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import fnmatch
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
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
# No system or global configuration, so no filter, diff or merge driver of the operator's runs on files a
# candidate commits.
ISOLATED = {'GIT_ATTR_NOSYSTEM': '1', 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}
# No hooks, filesystem monitor or global attributes file, which could hide content from the diff: nothing a
# repository holds runs.
GIT_OPTIONS = ('-c', 'core.attributesFile=/dev/null', '-c', 'core.fsmonitor=', '-c', 'core.hooksPath=/dev/null')
GATE = ('make', 'check')
# The only variables the gate sees, besides HOME and uv's cache, Pythons and offline switch.
GATE_ENVIRONMENT = ('PATH', 'LANG', 'LC_ALL', 'TERM', 'TMPDIR')
# Paths from the repository root. Make reads GNUmakefile or makefile before Makefile,
# and `make check` runs the two scripts named here.
PROTECTED = ('Makefile', 'GNUmakefile', 'makefile', 'uv.lock', '.python-version',
             'scripts/hooks/*', 'scripts/ci_*', 'scripts/check-naming.sh', 'scripts/check-packaged-runtimes.sh',
             '.github/*', 'docs/coverage.md', 'daydream/benchmarks/*', '.agents/*', 'AGENTS.md', 'CONTRIBUTING.md')
# Wherever they sit: tool settings, and files that take precedence over pyproject.toml's,
# .gitattributes, which can turn a file's diff into "Binary files differ", and the Git settings a person's Git
# reads when a winner is landed with it: .lfsconfig and .gitmodules.
SETTINGS_FILES = {'pyproject.toml', 'uv.toml', 'ruff.toml', '.ruff.toml', 'mypy.ini', '.mypy.ini',
                  'pytest.toml', '.pytest.toml', 'pytest.ini', '.pytest.ini', 'tox.ini', 'setup.cfg',
                  '.coveragerc', '.gitattributes', '.lfsconfig', '.gitmodules'}
KEYWORDS = re.compile('reviewbench|aacr|harbor', re.IGNORECASE)
# Ways to skip, expect failure, deselect or rewrite results in pytest and unittest.
# The bare word "skip" is common in Daydream's tests, so only call and marker forms count.
ESCAPE = re.compile(r'\bpytest\.(?:skip|xfail|importorskip)|\bmark\.(?:skip|xfail)|\.skip(?:if|unless|test)?\s*\('
                    r'|\bskip(?:if)?\(|xfail|importorskip|skiptest|expectedfailure|deselect|collect_ignore'
                    r'|^\s*(?:async\s+)?def\s+pytest_|pytest_plugins|__test__', re.IGNORECASE)
SKIP_IMPORTS = {'skip', 'skipif', 'skipIf', 'skipUnless', 'xfail', 'importorskip', 'expectedFailure', 'SkipTest'}
# coverage.py's default exclusion.
NO_COVER = re.compile(r'#\s*pragma[:\s]?\s*no\s*cover', re.IGNORECASE)
HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)')
DIFF = ('--no-renames', '--no-ext-diff', '--no-textconv', '--no-color')


def git(repository: Path, *arguments: str, input: str | None = None, environment: dict[str, str] | None = None) -> str:
    result = subprocess.run([GIT, *GIT_OPTIONS, '-C', str(repository), *arguments], capture_output=True, input=input,
                            encoding='utf-8', errors='surrogateescape',
                            env=os.environ | ISOLATED | (environment or {}))
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
    if not names:
        raise ValueError('The pinned sources gave no review case names')
    return names


def name_pattern(names: set[str]) -> re.Pattern:
    """Whole names in any letter case, so a short name never matches inside a longer one.
    A full stop or ``.git`` may follow, but not a longer dotted name such as ``next.js``."""
    if not names:
        raise ValueError('No names to match; an empty pattern would match every line')
    alternatives = '|'.join(re.escape(name) for name in sorted(names, key=len, reverse=True))
    return re.compile(rf'(?<![\w.-])(?:{alternatives})(?![\w-]|\.(?!git\b)\w)', re.IGNORECASE)


def protected(path: str) -> bool:
    # Casefolded: on a disk that ignores case, as macOS's does by default, .GITATTRIBUTES is .gitattributes.
    folded = path.casefold()
    return (PurePosixPath(folded).name in {name.casefold() for name in SETTINGS_FILES}
            or any(fnmatch.fnmatchcase(folded, rule.casefold()) for rule in PROTECTED))


def is_test_file(path: str) -> bool:
    name = PurePosixPath(path)
    return name.parts[0] == 'tests' and (fnmatch.fnmatchcase(name.name, 'test_*.py') or name.name.endswith('_test.py'))


def is_test_code(path: str) -> bool:
    name = PurePosixPath(path)
    return name.suffix == '.py' and (name.parts[0] == 'tests' or name.name == 'conftest.py')


def _parse(source: str) -> ast.Module:
    try:
        return ast.parse(source)
    except SyntaxError:
        return ast.Module(body=[], type_ignores=[])


def collected_tests(body: list[ast.stmt], prefix: str = '') -> set[str]:
    """The tests pytest collects by name: test* functions, and test* methods of Test* classes.
    A later binding of the same name replaces the test, as it does when the module runs."""
    names = set()

    def rebind(name: str) -> None:
        names.difference_update({known for known in names if known == prefix + name or known.startswith(f'{prefix}{name}.')})
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            rebind(node.name)
            if isinstance(node, ast.ClassDef) and node.name.startswith('Test'):
                names |= collected_tests(node.body, f'{prefix}{node.name}.')
            elif not isinstance(node, ast.ClassDef) and node.name.startswith('test'):
                names.add(prefix + node.name)
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and not isinstance(child.ctx, ast.Load):
                rebind(child.id)
            elif isinstance(child, ast.alias):
                rebind((child.asname or child.name).split('.')[0])
    return names


def skip_imports(tree: ast.Module) -> set[tuple[str, str]]:
    """Skip and xfail helpers imported by name from pytest or unittest, under any alias."""
    return {(node.module, alias.name) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            and (node.module or '').split('.')[0] in ('pytest', '_pytest', 'unittest')
            for alias in node.names if alias.name in SKIP_IMPORTS}


def added_lines(repository: Path, base: str, commit: str, path: str):
    """The new file's line number and text of each line the commit adds to path, binary or not."""
    number = None
    for line in git(repository, 'diff', '-U0', '--text', *DIFF, base, commit, '--', f':(literal){path}').splitlines():
        if hunk := HUNK.match(line):
            number = int(hunk[1])
        elif number is not None and line.startswith('+'):
            yield number, line[1:]
            number += 1


def uv_directories() -> dict[str, str]:
    """The operator's uv cache and Pythons, so the gate needs no downloads."""
    return {name: subprocess.run(['uv', *command], check=True, capture_output=True, text=True).stdout.strip()
            for name, command in (('UV_CACHE_DIR', ('cache', 'dir')), ('UV_PYTHON_INSTALL_DIR', ('python', 'dir')))}


def run_gate(checkout: Path, scratch: Path, timeout: int, deselect: list[str] = ()) -> dict:
    """Run Daydream's gate with a throwaway HOME, less the deselected tests, and kill whatever it leaves behind."""
    home, log = scratch / 'home', scratch / 'gate.log'
    home.mkdir()
    # uv works offline from the operator's cache, so the gate needs no proxy or credentials.
    environment = ({name: os.environ[name] for name in GATE_ENVIRONMENT if name in os.environ}
                   | uv_directories() | {'UV_OFFLINE': '1', 'HOME': str(home)})
    if deselect:
        # Daydream's `make test` passes PYTEST_ADDOPTS through to pytest.
        environment['PYTEST_ADDOPTS'] = shlex.join(f'--deselect={test}' for test in deselect)
    returncode, seconds = run_bounded(GATE, checkout, environment, log, timeout)
    return dict(command=' '.join(GATE), returncode=returncode, timed_out=returncode is None,
                seconds=round(seconds, 1), output_tail=log.read_text(encoding='utf-8', errors='replace')[-4000:])


def run_bounded(command, directory: Path, environment: dict[str, str], log: Path, timeout: float,
                stdin: Path | None = None) -> tuple[int | None, float]:
    """Run a command in a session of its own, output to log and input from stdin, and kill whatever it
    leaves behind. Return its exit code, None if it ran past the timeout, and its seconds."""
    started, timed_out = time.monotonic(), False
    # Files, not pipes: a descendant that leaves the process group can't hold them open.
    with log.open('wb') as output, (stdin.open('rb') if stdin else open(os.devnull, 'rb')) as source:
        process = subprocess.Popen(command, cwd=directory, env=environment, stdin=source,
                                   stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    return None if timed_out else process.returncode, time.monotonic() - started


def check(worktree: Path, base: str, commit: str, loaded: settings.Settings, names: set[str]) -> dict:
    """Refuse an unsafe or benchmark-specific commit; only then run the gate."""
    worktree = Path(worktree).resolve()
    base, commit = (git(worktree, 'rev-parse', '--verify', '--end-of-options', f'{revision}^{{commit}}').strip()
                    for revision in (base, commit))
    with tempfile.TemporaryDirectory(prefix='loop-guard-', ignore_cleanup_errors=True) as temporary:
        scratch = Path(temporary)
        checkout = scratch / 'daydream'
        git(scratch, 'clone', '--quiet', '--no-checkout', str(worktree), str(checkout))
        git(checkout, 'checkout', '--quiet', '--detach', commit)
        result = _check(checkout, scratch, base, commit, loaded, names)
    return dict(accepted=not result['refusals'], worktree=str(worktree), base=base, commit=commit) | result


def _check(checkout: Path, scratch: Path, base: str, commit: str, loaded: settings.Settings, names: set[str]) -> dict:
    refusals = []

    def refuse(kind: str, detail: str) -> None:
        refusals.append(dict(check=kind, detail=detail))

    parents = git(checkout, 'rev-list', '--parents', '-n', '1', commit).split()[1:]
    if parents != [base]:
        refuse('shape', f'Expected one commit on top of {base}; its parents are {parents}')
    fields = git(checkout, 'diff', '--name-status', '-z', *DIFF, base, commit).split('\0')[:-1]
    changes = dict(zip(fields[1::2], fields[::2]))
    counts = {path: (added, deleted) for added, deleted, path in
              (record.split('\t', 2) for record in
               git(checkout, 'diff', '--numstat', '-z', *DIFF, base, commit).split('\0') if record)}
    lines = sum(int(count) for pair in counts.values() for count in pair if count != '-')
    if not changes:
        refuse('shape', 'The commit changes no file')
    if len(changes) > loaded['max_diff_files']:
        refuse('shape', f'{len(changes)} files changed; at most {loaded["max_diff_files"]} may change')
    if lines > loaded['max_diff_lines']:
        refuse('shape', f'{lines} lines changed; at most {loaded["max_diff_lines"]} may change')
    pattern = name_pattern(names)
    for path, status in sorted(changes.items()):
        if '-' in counts.get(path, ()):
            refuse('shape', f'{path} is binary')
        if protected(path):
            refuse('paths', f'{path} may not change')
        if is_test_file(path) and status == 'D':
            refuse('tests', f'{path} is deleted')
        elif is_test_code(path) and status != 'D':
            before, after = (_parse(git(checkout, 'show', f'{revision}:{path}')) if exists else _parse('')
                             for revision, exists in ((base, status != 'A'), (commit, True)))
            tests_before, tests_after = len(collected_tests(before.body)), len(collected_tests(after.body))
            if is_test_file(path) and tests_after < tests_before:
                refuse('tests', f'{path} has {tests_after} test functions, fewer than its {tests_before}')
            if skip_imports(after) - skip_imports(before):
                refuse('tests', f'{path} imports a skip or xfail helper by name')
        found = {}
        for number, line in added_lines(checkout, base, commit, path):
            if keyword := KEYWORDS.search(line):
                found.setdefault(('benchmark', f'adds {keyword[0].lower()!r}'), []).append(number)
            # The name stays out of the ledger, which the proposer may read.
            if pattern.search(line):
                found.setdefault(('benchmark', 'names a review case or its repository'), []).append(number)
            if is_test_code(path) and ESCAPE.search(line):
                found.setdefault(('tests', 'adds a skip, xfail, deselection or pytest hook'), []).append(number)
            if NO_COVER.search(line):
                found.setdefault(('tests', 'adds a coverage exclusion'), []).append(number)
        for (kind, finding), numbers in found.items():
            more = f' (and {len(numbers) - 1} more lines)' if len(numbers) > 1 else ''
            refuse(kind, f'{path}:{numbers[0]} {finding}{more}')
    gate = None
    if not refusals:
        gate = run_gate(checkout, scratch, loaded['gate_timeout_seconds'],
                        loaded['gate_deselect_macos'] if sys.platform == 'darwin' else [])
        if gate['timed_out']:
            refuse('gate', f'make check ran past {loaded["gate_timeout_seconds"]} seconds')
        elif gate['returncode']:
            refuse('gate', f'make check exited {gate["returncode"]}')
    return dict(files=len(changes), lines=lines, refusals=refusals, gate=gate)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('worktree', type=Path, help="A Daydream worktree whose repository holds the commit")
    parser.add_argument('base', help='The Daydream commit the candidate builds on')
    parser.add_argument('commit', help="The proposer's commit")
    parser.add_argument('--reviewbench', type=Path, default=REVIEWBENCH, help='Clean checkout of the pinned ReviewBench')
    parser.add_argument('--aacr', type=Path, default=AACR, help='Clean checkout of the pinned AACR-Bench')
    arguments = parser.parse_args(argv)
    try:
        names = benchmark_names(arguments.reviewbench, arguments.aacr)
        result = check(arguments.worktree, arguments.base, arguments.commit, settings.load(), names)
    except (OSError, RuntimeError, ValueError) as error:
        # Not a refusal of the change: the guard couldn't check it. The ledger still gets JSON.
        print(json.dumps(dict(accepted=False, error=str(error)), indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result['accepted'] else 1


if __name__ == '__main__':
    sys.exit(main())
