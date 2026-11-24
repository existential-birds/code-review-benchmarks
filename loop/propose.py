"""Propose one change to Daydream: an agent edits a copy of the baseline, and the loop commits it.

    .venv/bin/python -m loop.propose --run <run> --baseline <daydream-commit> --evidence <pack>

The loop keeps its own clone of Daydream in ``.cache/loop/daydream``, never a person's
working clone: made if absent, then fetched. A baseline given by name resolves as the
remote has it. The iteration is the next one in ``loop-runs/<run>/ledger.jsonl``; its
worktree is ``.cache/loop/worktrees/<run>-<n>`` on the branch ``loop/<run>/<n>``, cut
from the baseline commit.

The proposer never touches that worktree or any Git folder. It runs ``proposer_command``
from ``loop/settings.toml`` in a container of its own, with the prompt on stdin, for at
most ``--timeout`` seconds an attempt (two hours by default). The container holds only:

- ``/work``: an export of the baseline commit, no Git folder, for it to edit;
- ``/pack``: a read-only copy of the evidence pack;
- ``/analysis``, for the trace-analysis skill's files, and ``/out``, for ``proposal.json``;
- a Codex home holding only a config and the reviewer's Codex login, as the review tasks
  mount it.

So this repository's ``jobs/``, ``artifacts/``, ``loop-runs/`` and ``confirmations/``, and
anything held out, are out of its reach: Codex's own sandbox limits only what it writes.
The container reaches the network, for Codex's model; a hostile proposer could send the
Codex login elsewhere, as a hostile review case could. Its image, built on first use from
the reviewer's pinned Node image, Python and Codex CLI, holds no Daydream.

The loop then copies the edited files into the worktree, refusing a link or special file
that the baseline doesn't have, a ``.git`` entry, and a path that leaves the worktree,
and commits them once with the ``message`` from ``proposal.json``. Git runs with hooks
off, so nothing the proposer wrote runs.

The prompt joins ``loop/proposer.md``, the container's paths, the diff limits and the
guard's protected paths, ``loop.ledger ideas`` for every run in ``loop-runs/``, and
``loop/seeds.md``. An attempt is refused when the proposer fails or times out, when it
changed nothing, when ``proposal.json`` is invalid or doesn't list the commit's files,
when the commit's ``git patch-id --stable`` equals an earlier iteration's, or when its
category and files equal a rejected iteration's that ``differs_from`` doesn't name. A
refusal resets the worktree and the copy to the baseline and runs the proposer again
with every refusal so far; after ``max_proposal_attempts`` it gives up. Each attempt's
prompt and output stay in ``.cache/loop/logs/<run>-<n>/``.

Prints JSON for the ledger and exits 0 with a proposal, 1 without one, or 2 with an
``error`` when it couldn't set up or run the step.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

from benchmarks.daydream import build
from loop import evidence, guard, ledger, settings

ROOT = Path(__file__).resolve().parents[1]
CLONE_URL = 'https://github.com/existential-birds/daydream'
PROMPT, SEEDS = Path(__file__).with_name('proposer.md'), Path(__file__).with_name('seeds.md')
SKILL = '.agents/skills/daydream-trace-analysis'
TIMEOUT_SECONDS = 2 * 60 * 60
# Passed into the container: the proxy the agent reaches its model through.
ENVIRONMENT = ('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy', 'NO_PROXY', 'no_proxy')
# Where the container sees each folder, and the variables naming them.
PATHS = dict(WORK='/work', PACK='/pack', ANALYSIS='/analysis', PROPOSAL='/out/proposal.json')
CODEX_CONFIG = 'model_provider = "openai"\ncli_auth_credentials_store = "file"\n'
DOCKERFILE = f'''FROM {build.NODE_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git ripgrep \\
    && rm -rf /var/lib/apt/lists/*
COPY --from={build.UV_IMAGE} /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_LINK_MODE=copy UV_NO_CACHE=1
RUN uv python install {build.PYTHON} && ln -s "$(uv python find {build.PYTHON})" /usr/local/bin/python3
RUN npm install --global --ignore-scripts @openai/codex@{build.BUILD['codex_cli']}
RUN python3 --version && codex --version
'''
IMAGE = 'daydream-loop-proposer:' + hashlib.sha256(DOCKERFILE.encode()).hexdigest()[:16]
# Larger than any source file the harness holds; reading it whole stays cheap.
MAX_FILE_BYTES = 5 * 1024 * 1024
# A Conventional Commit's first line.
MESSAGE = re.compile(r'(build|chore|docs|feat|fix|perf|refactor|style|test)(\([a-z0-9._/-]+\))?!?: \S.*')
# The trace-analysis skill's categories (scripts/reports.py's CATEGORIES in Daydream 0.37.0).
CATEGORIES = ('environment_scope', 'investigation_loop', 'handoff_loss', 'verification_gap',
              'budget_allocation', 'finding_quality', 'backend_lifecycle', 'capture_failure')
FIELDS = {'hypothesis', 'category', 'change', 'prediction', 'quality_guardrails', 'rejection_criterion',
          'evidence', 'files', 'message'}
# The skill's limits on a text and on a pattern name.
MAX_TEXT, MAX_PATTERN = 2000, 80


def _text(value: object, name: str, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f'proposal.json: {name} must be nonempty text of at most {limit} characters')
    return value


def _texts(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f'proposal.json: {name} must be a nonempty list')
    for item in value:
        _text(item, name)
    if len(set(value)) != len(value):
        raise ValueError(f'proposal.json: {name} repeats a value')
    return value


def read_proposal(path: Path, pack: Path) -> dict:
    """The proposer's proposal.json, checked on its own and against the pack."""
    try:
        proposal = json.loads(path.read_text(), parse_constant=ledger._refuse_constant)
    except FileNotFoundError:
        raise ValueError(f'No proposal.json at {path}') from None
    except (OSError, ValueError) as error:
        raise ValueError(f'proposal.json is not strict JSON: {error}') from None
    if not isinstance(proposal, dict):
        raise ValueError('proposal.json must hold one JSON object')
    if missing := sorted(FIELDS - set(proposal)):
        raise ValueError(f'proposal.json lacks {missing}')
    if unknown := sorted(set(proposal) - FIELDS - {'differs_from'}):
        raise ValueError(f'proposal.json has unknown fields {unknown}')
    for name in ('hypothesis', 'prediction', 'rejection_criterion'):
        _text(proposal[name], name)
    if not MESSAGE.fullmatch(_text(proposal['message'], 'message').splitlines()[0]):
        raise ValueError('proposal.json: message must start with a Conventional Commit line, type(scope): summary')
    if proposal['category'] not in CATEGORIES:
        raise ValueError(f'proposal.json: category must be one of {list(CATEGORIES)}')
    change = proposal['change']
    if not isinstance(change, dict) or set(change) != {'pattern', 'description'}:
        raise ValueError('proposal.json: change must be {"pattern": ..., "description": ...}')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', _text(change['pattern'], 'change.pattern', MAX_PATTERN)):
        raise ValueError('proposal.json: change.pattern must be a lowercase slug')
    _text(change['description'], 'change.description')
    _texts(proposal['quality_guardrails'], 'quality_guardrails')
    _texts(proposal['files'], 'files')
    for pointer in _texts(proposal['evidence'], 'evidence'):
        target = PurePosixPath(pointer.split('#', 1)[0])
        if target.is_absolute() or '..' in target.parts or not (pack / target).exists():
            raise ValueError(f'proposal.json: evidence {pointer!r} names no file in the pack')
    differs = proposal.get('differs_from', [])
    if not isinstance(differs, list) or not all(
            isinstance(item, dict) and set(item) == {'run', 'iteration', 'how'} and isinstance(item['run'], str)
            and type(item['iteration']) is int and isinstance(item['how'], str) and item['how'].strip()
            for item in differs):
        raise ValueError('proposal.json: differs_from must be a list of {"run", "iteration", "how"}')
    return proposal


def patch_id(repository: Path, base: str, commit: str) -> str:
    """``git patch-id --stable`` of the commit's diff from the base."""
    # --text: a committed .gitattributes can't make a repeat look binary, and so new.
    diff = guard.git(repository, 'diff', '--text', *guard.DIFF, base, commit)
    return guard.git(repository, 'patch-id', '--stable', input=diff).split()[0]


def diff_size(repository: Path, base: str, commit: str) -> tuple[list[str], int]:
    """The files the commit changes and its lines added and deleted, as the guard counts them."""
    records = [record.split('\t', 2) for record in
               guard.git(repository, 'diff', '--numstat', '-z', '--text', *guard.DIFF, base, commit).split('\0')
               if record]
    return sorted(path for _, _, path in records), sum(int(count) for *counts, _ in records
                                                        for count in counts if count != '-')


def earlier_iterations(runs: list[Path]) -> list[tuple[str, dict]]:
    return [(run.name, entry) for run in runs for entry in ledger.read(run) if entry['kind'] == 'iteration']


def check_attempt(worktree: Path, base: str, commit: str, proposal: dict, pack: Path,
                  earlier: list[tuple[str, dict]]) -> dict:
    """The attempt's commit checked against the proposal and earlier iterations, or a ValueError saying why it is
    refused."""
    files, lines = diff_size(worktree, base, commit)
    patch = patch_id(worktree, base, commit)
    for run, entry in earlier:
        if (entry.get('candidate') or {}).get('patch_id') == patch:
            raise ValueError(f"The change repeats the patch of {run} #{entry['iteration']}")
    if sorted(proposal['files']) != files:
        raise ValueError(f"proposal.json: files must list exactly the files the change touches, {files}")
    named = {(item['run'], item['iteration']) for item in proposal.get('differs_from', [])}
    for run, entry in earlier:
        before = entry.get('proposal') or {}
        if (entry['verdict']['verdict'] == 'rejected' and before.get('category') == proposal['category']
                and sorted(before.get('files', [])) == files and (run, entry['iteration']) not in named):
            raise ValueError(f"Category {proposal['category']} and files {files} are those of rejected "
                             f"{run} #{entry['iteration']}; change something else, or name it in differs_from "
                             'and say how this differs')
    return dict(proposal=proposal, commit=commit, patch_id=patch, diff=dict(files=len(files), lines=lines))


def prompt(branch: str, base: str, ideas: list[str], loaded: settings.Settings) -> str:
    """Everything the proposer is told, except the refusals of earlier attempts."""
    variables = ''.join(f'    {name}={path}\n' for name, path in (PATHS | dict(SKILL=f"{PATHS['WORK']}/{SKILL}")).items())
    return f"""{PROMPT.read_text()}
## This iteration

`$WORK` is a copy of Daydream at the baseline commit `{base}`, which becomes the
branch `{branch}`. It is not a Git repository: edit the files, and the loop commits
them. Each shell command starts afresh, so set the variables it uses:

{variables}
`$PACK` is a read-only copy of the evidence pack; `$ANALYSIS` is yours for the skill's
files; write `proposal.json` at `$PROPOSAL`.

Limits:

- At most {loaded['max_diff_files']} files and {loaded['max_diff_lines']} lines changed (added plus deleted), and no binary file.
- Never change these paths: {', '.join(f'`{rule}`' for rule in guard.PROTECTED)}.
- Never change these files, in any folder: {', '.join(f'`{name}`' for name in sorted(guard.SETTINGS_FILES))}.
- Add no link and no `.git` entry.
- A candidate whose reviews cost more than {loaded['max_cost_ratio']} times the baseline's is held for a person.

## Ideas tried before

One line each: run and iteration, verdict, category, hypothesis, files and reasons.

{chr(10).join(f'- {line}' for line in ideas) or 'None yet.'}

{SEEDS.read_text()}"""


def refusal_text(refusals: list[dict]) -> str:
    if not refusals:
        return ''
    lines = ''.join(f"- Attempt {refusal['attempt']}: {refusal['reason']}\n" for refusal in refusals)
    return ('\n## Refused attempts\n\nThese attempts were refused, and `$WORK` is back at the baseline. '
            f'Fix what was refused:\n\n{lines}')


def clone(root: Path, url: str, baseline: str) -> tuple[Path, str]:
    """The loop's own clone, made if absent and fetched, and the baseline commit in it."""
    repository = root / '.cache/loop/daydream'
    if not (repository / '.git').exists():
        repository.parent.mkdir(parents=True, exist_ok=True)
        guard.git(repository.parent, 'clone', '--quiet', '--no-checkout', url, str(repository))
    guard.git(repository, 'fetch', '--quiet', '--tags', '--force', '--prune', 'origin')
    # A name resolves as the remote has it now, never as the clone first saw it.
    candidates = [baseline] if re.fullmatch(r'[0-9a-f]{7,64}', baseline) else [f'refs/remotes/origin/{baseline}',
                                                                               f'refs/tags/{baseline}']
    for candidate in candidates:
        try:
            return repository, guard.git(repository, 'rev-parse', '--verify', '--end-of-options',
                                         f'{candidate}^{{commit}}').strip()
        except RuntimeError:
            continue
    raise ValueError(f'No commit, remote branch or tag {baseline!r} in {url}')


def image() -> str:
    """The proposer's image, built from DOCKERFILE when absent."""
    if subprocess.run(['docker', 'image', 'inspect', IMAGE], capture_output=True).returncode:
        subprocess.run(['docker', 'build', '--quiet', '--tag', IMAGE, '-'], input=DOCKERFILE.encode(), check=True,
                       capture_output=True)
    return IMAGE


def codex_login() -> Path:
    """The reviewer's Codex login file, as the review tasks mount it."""
    folder = os.environ.get('REVIEWER_CODEX_AUTH_DIR') or os.environ.get('CODEX_HOME') or str(Path.home() / '.codex')
    login = Path(folder) / 'auth.json'
    if not login.is_file():
        raise ValueError(f'No Codex login at {login}; sign in with codex login, or set REVIEWER_CODEX_AUTH_DIR')
    return login


def container(argv: list[str], scratch: Path, name: str) -> list[str]:
    """The docker command running the proposer with only its folders and the Codex login mounted."""
    mounts = [(scratch / 'work', PATHS['WORK'], 'rw'), (scratch / 'evidence', PATHS['PACK'], 'ro'),
              (scratch / 'analysis', PATHS['ANALYSIS'], 'rw'), (scratch / 'out', '/out', 'rw'),
              (scratch / 'codex', '/codex', 'rw'), (codex_login(), '/codex/auth.json', 'rw')]
    variables = {name: os.environ[name] for name in ENVIRONMENT if name in os.environ} | dict(
        CODEX_HOME='/codex', HOME='/tmp', **PATHS)
    return ['docker', 'run', '--rm', '--interactive', '--init', '--name', name, '--workdir', PATHS['WORK'],
            # As the operator, so every file it leaves can be removed; with no privileges to gain.
            '--user', f'{os.getuid()}:{os.getgid()}', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--pids-limit', '1024', '--memory', '8g',
            *[part for source, target, mode in mounts for part in ('--volume', f'{source}:{target}:{mode}')],
            *[part for key, value in variables.items() for part in ('--env', f'{key}={value}')],
            image(), *argv]


def stop(name: str) -> None:
    subprocess.run(['docker', 'rm', '--force', name], capture_output=True)


def export(repository: Path, base: str, target: Path) -> None:
    """The baseline's tracked files, with no Git folder, for the proposer to edit."""
    target.mkdir()
    archive = subprocess.run([guard.GIT, *guard.GIT_OPTIONS, '-C', str(repository), 'archive', '--format=tar', base],
                             capture_output=True, check=True, env=os.environ | guard.ISOLATED).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(target, filter='tar')


def _entries(folder: Path) -> dict[str, os.stat_result]:
    """Every file and link under a folder, by relative path, without following links."""
    found = {}
    for directory, folders, files in os.walk(folder):
        for name in folders + files:
            path = Path(directory) / name
            info = path.lstat()
            # A folder is listed only when it is a Git folder, which a change may never add.
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or name.casefold() == '.git':
                found[path.relative_to(folder).as_posix()] = info
        folders[:] = [name for name in folders if not (Path(directory) / name).is_symlink()]
    return found


def apply(work: Path, worktree: Path) -> None:
    """Make the worktree's files the proposer's edited copy, refusing what a review change can't be."""
    edited, current = _entries(work), _entries(worktree)
    # The worktree's own .git file is Git's, not part of the change.
    current = {path: info for path, info in current.items() if path != '.git' and not path.startswith('.git/')}
    for path, info in edited.items():
        # Casefolded: on a disk that ignores case, as macOS's does by default, .GIT is .git.
        if '.git' in (part.casefold() for part in PurePosixPath(path).parts):
            raise ValueError(f'The change adds a .git entry: {path}')
        if info.st_size > MAX_FILE_BYTES:
            raise ValueError(f'The change holds a file over {MAX_FILE_BYTES} bytes: {path}')
        if stat.S_ISLNK(info.st_mode):
            if path not in current or os.readlink(work / path) != os.readlink(worktree / path):
                raise ValueError(f'The change adds or alters a link: {path}')
        elif not stat.S_ISREG(info.st_mode):
            raise ValueError(f'The change adds a special file: {path}')
        elif path in current and stat.S_ISLNK(current[path].st_mode):
            raise ValueError(f'The change replaces a link with a file: {path}')
    for path, info in current.items():
        if path not in edited:
            (worktree / path).unlink()
    for path, info in edited.items():
        if stat.S_ISLNK(info.st_mode):
            continue
        target = worktree / path
        if not target.parent.resolve().is_relative_to(worktree.resolve()):
            raise ValueError(f'The change writes outside the worktree: {path}')
        if target.exists() and os.path.samefile(target, worktree / '.git'):
            raise ValueError(f'The change writes over the worktree\'s Git file: {path}')
        if target.is_dir() and not target.is_symlink():
            # A folder of the baseline replaced by a file; its files were deleted above.
            shutil.rmtree(target)
        data = (work / path).read_bytes()
        if not target.is_file() or target.read_bytes() != data:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        target.chmod(0o755 if info.st_mode & stat.S_IXUSR else 0o644)


def commit_change(worktree: Path, message: str) -> str:
    """Commit the worktree's changes once, as the loop, and return the commit."""
    guard.git(worktree, 'add', '--all')
    if not guard.git(worktree, 'status', '--porcelain'):
        raise ValueError('The proposer changed nothing; change one thing in $WORK')
    # Set as variables, which outrank any identity in the operator's environment or configuration.
    identity = {f'GIT_{role}_{key}': value for role in ('AUTHOR', 'COMMITTER')
                for key, value in (('NAME', 'Daydream loop proposer'), ('EMAIL', 'proposer@daydream-loop.invalid'))}
    guard.git(worktree, 'commit', '--quiet', '--no-verify', '--file=-', input=message.rstrip('\n') + '\n',
              environment=identity)
    return guard.git(worktree, 'rev-parse', 'HEAD').strip()


def copy_pack(pack: Path, target: Path) -> None:
    """A read-only copy of the evidence pack, refusing links and anything that isn't one."""
    if not (pack / 'index.json').is_file() or not (pack / 'labels.jsonl').is_file():
        raise ValueError(f'{pack} is not an evidence pack from python -m loop.evidence')
    if links := [path for path in pack.rglob('*') if path.is_symlink()]:
        raise ValueError(f'Refusing an evidence pack with links: {links[0]}')
    shutil.copytree(pack, target)
    evidence._set_mode(target, 0o444, 0o555)


def _remove(folder: Path) -> None:
    """Remove a scratch folder, read-only parts too, without following links into other places."""
    for path in [folder, *folder.rglob('*')]:
        if path.is_dir() and not path.is_symlink():
            path.chmod(0o755)
    shutil.rmtree(folder, ignore_errors=True)


def propose(run: str, baseline: str, pack: Path, loaded: settings.Settings, *, root: Path = ROOT,
            url: str = CLONE_URL, timeout: float = TIMEOUT_SECONDS) -> dict:
    """Run the proposer until it makes an acceptable change or uses up its attempts."""
    runs = sorted(path.parent for path in (root / 'loop-runs').glob('*/ledger.jsonl'))
    earlier = earlier_iterations(runs)
    n = 1 + max((entry['iteration'] for name, entry in earlier if name == run), default=0)
    repository, base = clone(root, url, baseline)
    branch, worktree = f'loop/{run}/{n}', root / '.cache/loop/worktrees' / f'{run}-{n}'
    guard.git(repository, 'worktree', 'prune')
    if worktree.exists() or guard.git(repository, 'branch', '--list', branch).strip():
        raise ValueError(f'{worktree} or the branch {branch} already exists: iteration {n} was proposed '
                         'without a ledger entry. Record it, or remove both')
    logs = root / '.cache/loop/logs' / f'{run}-{n}'
    scratch, made = Path(tempfile.mkdtemp(prefix='loop-propose-')), False
    try:
        copy_pack(Path(pack), scratch / 'evidence')
        guard.git(repository, 'worktree', 'add', '--quiet', '-b', branch, str(worktree), base)
        made = True
        logs.mkdir(parents=True, exist_ok=True)
        ideas = ledger.ideas(runs)
        result = dict(run=run, iteration=n, baseline=base, branch=branch, worktree=str(worktree), logs=str(logs),
                      proposal=None, commit=None, patch_id=None, diff=None, attempts=0, refusals=[])
        for attempt in range(1, loaded['max_proposal_attempts'] + 1):
            result['attempts'] = attempt
            for folder in ('work', 'analysis', 'out', 'codex'):
                _remove(scratch / folder)
            export(repository, base, scratch / 'work')
            for folder in ('analysis', 'out', 'codex'):
                (scratch / folder).mkdir()
            (scratch / 'codex/config.toml').write_text(CODEX_CONFIG)
            stdin = logs / f'attempt-{attempt}.prompt.md'
            stdin.write_text(prompt(branch, base, ideas, loaded) + refusal_text(result['refusals']))
            name = f'loop-propose-{run}-{n}-{attempt}'
            try:
                returncode, _ = guard.run_bounded(launch(loaded['proposer_command'], scratch, name), scratch,
                                                  dict(os.environ), logs / f'attempt-{attempt}.log', timeout,
                                                  stdin=stdin)
            finally:
                stop(name)
            try:
                if returncode is None:
                    raise ValueError(f'The proposer ran past {timeout} seconds')
                if returncode:
                    raise ValueError(f'The proposer exited {returncode}')
                proposal = read_proposal(scratch / 'out/proposal.json', scratch / 'evidence')
                apply(scratch / 'work', worktree)
                commit = commit_change(worktree, proposal['message'])
                return result | check_attempt(worktree, base, commit, proposal, scratch / 'evidence', earlier)
            except (ValueError, RuntimeError, OSError, MemoryError) as error:
                result['refusals'].append(dict(attempt=attempt, reason=str(error)))
                guard.git(worktree, 'checkout', '--quiet', '--force', '-B', branch, base)
                guard.git(worktree, 'clean', '-ffdxq')
        return result | dict(reason=f'No proposal: all {result["attempts"]} attempts were refused')
    except BaseException:
        # Set-up or the container failed, not the proposer: leave nothing that blocks the next try.
        if made:
            guard.git(repository, 'worktree', 'remove', '--force', str(worktree))
            guard.git(repository, 'branch', '--delete', '--force', branch)
        raise
    finally:
        _remove(scratch)


# How each attempt's command is made; tests run a stand-in on the host instead.
launch = container


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', required=True, type=ledger._run_name, help='The run, a folder name under loop-runs/')
    parser.add_argument('--baseline', required=True, help='The Daydream commit to change')
    parser.add_argument('--evidence', required=True, type=Path, help='The evidence pack from python -m loop.evidence')
    parser.add_argument('--timeout', type=float, default=TIMEOUT_SECONDS, help='Seconds each proposer attempt may run')
    arguments = parser.parse_args(argv)
    try:
        result = propose(arguments.run, arguments.baseline, arguments.evidence, settings.load(),
                         root=ROOT, url=CLONE_URL, timeout=arguments.timeout)
    except Exception as error:
        print(json.dumps(dict(error=f'{type(error).__name__}: {error}'), indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result['proposal'] else 1


if __name__ == '__main__':
    sys.exit(main())
