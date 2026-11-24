"""Propose one change to Daydream: an agent commits it in a worktree of the loop's own clone.

    .venv/bin/python -m loop.propose --run <run> --baseline <daydream-commit> --evidence <pack>

The loop keeps its own clone of Daydream in ``.cache/loop/daydream``, never a person's
working clone: made if absent, then fetched. The iteration is the next one in
``loop-runs/<run>/ledger.jsonl``; its worktree is ``.cache/loop/worktrees/<run>-<n>`` on
the branch ``loop/<run>/<n>``, cut from the baseline commit.

The proposer is ``proposer_command`` from ``loop/settings.toml``, run in the worktree
with the prompt on stdin, as ``codex exec`` takes it when given no prompt argument, for
at most ``--timeout`` seconds an attempt (two hours by default). It gets a throwaway
HOME, Codex's login folder and an allowlisted environment; the sandbox flags in
``proposer_command`` decide what else it may write or reach. For Codex the clone's Git
folder is added as writable, since the worktree's one commit lands there. A sandbox that limits
writes, as Codex's ``workspace-write`` does, may still let it read other files on the
machine; it is shown only the copies. The prompt joins ``loop/proposer.md``, the paths
of a fresh read-only copy of the evidence pack and of ``proposal.json``, both in a
temporary folder outside this repository, the diff limits and the guard's protected
paths, ``loop.ledger ideas`` for every run in ``loop-runs/``, and ``loop/seeds.md``.

An attempt is refused when the proposer fails or times out, when it leaves anything but
exactly one commit on top of the baseline on its branch, when ``proposal.json`` is
invalid or doesn't list the commit's files, when the commit's ``git patch-id --stable``
equals an earlier iteration's, or when its category and files equal a rejected
iteration's that ``differs_from`` doesn't name. A refusal resets the worktree to the
baseline and runs the proposer again with every refusal so far; after
``max_proposal_attempts`` it gives up. Each attempt's prompt and output stay in
``.cache/loop/logs/<run>-<n>/``.

Prints JSON for the ledger and exits 0 with a proposal, 1 without one, or 2 with an
``error`` when it couldn't set up or run the step.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import sys
import tempfile

from loop import evidence, guard, ledger, settings

ROOT = Path(__file__).resolve().parents[1]
CLONE_URL = 'https://github.com/existential-birds/daydream'
PROMPT, SEEDS = Path(__file__).with_name('proposer.md'), Path(__file__).with_name('seeds.md')
SKILL = '.agents/skills/daydream-trace-analysis'
TIMEOUT_SECONDS = 2 * 60 * 60
# Besides a throwaway HOME and CODEX_HOME: the gate's variables, and the proxy and
# certificates the agent itself reaches its model through.
ENVIRONMENT = (*guard.GATE_ENVIRONMENT, 'HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy',
               'NO_PROXY', 'no_proxy', 'SSL_CERT_FILE')
# The trace-analysis skill's categories (scripts/reports.py's CATEGORIES in Daydream 0.37.0).
CATEGORIES = ('environment_scope', 'investigation_loop', 'handoff_loss', 'verification_gap',
              'budget_allocation', 'finding_quality', 'backend_lifecycle', 'capture_failure')
FIELDS = {'hypothesis', 'category', 'change', 'prediction', 'quality_guardrails', 'rejection_criterion',
          'evidence', 'files'}
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


def read_proposal(path: Path, files: list[str], pack: Path) -> dict:
    """The proposer's proposal.json, checked against the commit's files and the pack."""
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
    if proposal['category'] not in CATEGORIES:
        raise ValueError(f'proposal.json: category must be one of {list(CATEGORIES)}')
    change = proposal['change']
    if not isinstance(change, dict) or set(change) != {'pattern', 'description'}:
        raise ValueError('proposal.json: change must be {"pattern": ..., "description": ...}')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', _text(change['pattern'], 'change.pattern', MAX_PATTERN)):
        raise ValueError('proposal.json: change.pattern must be a lowercase slug')
    _text(change['description'], 'change.description')
    _texts(proposal['quality_guardrails'], 'quality_guardrails')
    if sorted(_texts(proposal['files'], 'files')) != sorted(files):
        raise ValueError(f"proposal.json: files must list exactly the commit's files, {sorted(files)}")
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
    diff = guard.git(repository, 'diff', *guard.DIFF, base, commit)
    return guard.git(repository, 'patch-id', '--stable', input=diff).split()[0]


def diff_size(repository: Path, base: str, commit: str) -> tuple[list[str], int]:
    """The files the commit changes and its lines added and deleted, as the guard counts them."""
    records = [record.split('\t', 2) for record in
               guard.git(repository, 'diff', '--numstat', '-z', *guard.DIFF, base, commit).split('\0') if record]
    return sorted(path for _, _, path in records), sum(int(count) for *counts, _ in records
                                                        for count in counts if count != '-')


def earlier_iterations(runs: list[Path]) -> list[tuple[str, dict]]:
    return [(run.name, entry) for run in runs for entry in ledger.read(run) if entry['kind'] == 'iteration']


def check_attempt(worktree: Path, branch: str, base: str, proposal_path: Path, pack: Path,
                  earlier: list[tuple[str, dict]]) -> dict:
    """The attempt's commit and proposal, or a ValueError saying why it is refused."""
    if guard.git(worktree, 'rev-parse', '--symbolic-full-name', 'HEAD').strip() != f'refs/heads/{branch}':
        raise ValueError(f'The worktree is not on its branch {branch}; commit on that branch')
    commit = guard.git(worktree, 'rev-parse', 'HEAD').strip()
    if commit == base:
        raise ValueError('The proposer made no commit; make exactly one on top of the baseline')
    if guard.git(worktree, 'rev-list', '--parents', '-n', '1', commit).split()[1:] != [base]:
        count = guard.git(worktree, 'rev-list', '--count', f'{base}..{commit}').strip()
        raise ValueError(f'The proposer made {count} commits; make exactly one on top of the baseline')
    files, lines = diff_size(worktree, base, commit)
    if not files:
        raise ValueError('The commit changes no file')
    patch = patch_id(worktree, base, commit)
    for run, entry in earlier:
        if (entry.get('candidate') or {}).get('patch_id') == patch:
            raise ValueError(f"The commit repeats the patch of {run} #{entry['iteration']}")
    proposal = read_proposal(proposal_path, files, pack)
    named = {(item['run'], item['iteration']) for item in proposal.get('differs_from', [])}
    for run, entry in earlier:
        before = entry.get('proposal') or {}
        if (entry['verdict']['verdict'] == 'rejected' and before.get('category') == proposal['category']
                and sorted(before.get('files', [])) == files and (run, entry['iteration']) not in named):
            raise ValueError(f"Category {proposal['category']} and files {files} are those of rejected "
                             f"{run} #{entry['iteration']}; change something else, or name it in differs_from "
                             'and say how this differs')
    return dict(proposal=proposal, commit=commit, patch_id=patch, diff=dict(files=len(files), lines=lines))


def prompt(worktree: Path, branch: str, base: str, paths: dict[str, Path], ideas: list[str],
           loaded: settings.Settings) -> str:
    """Everything the proposer is told, except the refusals of earlier attempts."""
    variables = ''.join(f'    {name}={shlex.quote(str(path))}\n' for name, path in paths.items())
    return f"""{PROMPT.read_text()}
## This iteration

Your working directory, {shlex.quote(str(worktree))}, is a worktree of Daydream on the
branch `{branch}` at the baseline commit `{base}`. Each shell command starts
afresh, so set the variables it uses:

{variables}
`$PACK` is a read-only copy of the evidence pack; `$ANALYSIS` is yours for the skill's
files; write `proposal.json` at `$PROPOSAL`.

Limits:

- At most {loaded['max_diff_files']} files and {loaded['max_diff_lines']} lines changed (added plus deleted), and no binary file.
- Never change these paths: {', '.join(f'`{rule}`' for rule in guard.PROTECTED)}.
- Never change these files, in any folder: {', '.join(f'`{name}`' for name in sorted(guard.SETTINGS_FILES))}.
- A candidate whose reviews cost more than {loaded['max_cost_ratio']} times the baseline's is held for a person.

## Ideas tried before

One line each: run and iteration, verdict, category, hypothesis, files and reasons.

{chr(10).join(f'- {line}' for line in ideas) or 'None yet.'}

{SEEDS.read_text()}"""


def refusal_text(refusals: list[dict]) -> str:
    if not refusals:
        return ''
    lines = ''.join(f"- Attempt {refusal['attempt']}: {refusal['reason']}\n" for refusal in refusals)
    return ('\n## Refused attempts\n\nThese attempts were refused, and the worktree is back at the baseline. '
            f'Fix what was refused:\n\n{lines}')


def clone(root: Path, url: str, baseline: str) -> tuple[Path, str]:
    """The loop's own clone, made if absent and fetched, and the baseline commit in it."""
    repository = root / '.cache/loop/daydream'
    if not (repository / '.git').exists():
        repository.parent.mkdir(parents=True, exist_ok=True)
        guard.git(repository.parent, 'clone', '--quiet', '--no-checkout', url, str(repository))
    # The proposer runs with a throwaway HOME, so its commits take this identity.
    guard.git(repository, 'config', 'user.name', 'Daydream loop proposer')
    guard.git(repository, 'config', 'user.email', 'proposer@daydream-loop.invalid')
    guard.git(repository, 'fetch', '--quiet', '--tags', 'origin')
    return repository, guard.git(repository, 'rev-parse', '--verify', '--end-of-options', f'{baseline}^{{commit}}').strip()


def command(argv: list[str], repository: Path) -> list[str]:
    """The proposer command. Codex's workspace-write sandbox lets it write only the worktree, but a
    worktree's commit lands in the clone's Git folder, so that folder is added as writable too."""
    if Path(argv[0]).name == 'codex' and 'exec' in argv:
        at = argv.index('exec') + 1
        return [*argv[:at], '--add-dir', str((Path(repository) / '.git').resolve()), *argv[at:]]
    return list(argv)


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
    """Run the proposer until it makes an acceptable commit or uses up its attempts."""
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
    scratch = Path(tempfile.mkdtemp(prefix='loop-propose-'))
    try:
        paths = dict(SKILL=worktree / SKILL, PACK=scratch / 'evidence', ANALYSIS=scratch / 'analysis')
        copy_pack(Path(pack), paths['PACK'])
        guard.git(repository, 'worktree', 'add', '--quiet', '-b', branch, str(worktree), base)
        logs.mkdir(parents=True, exist_ok=True)
        for folder in ('analysis', 'home'):
            (scratch / folder).mkdir()
        environment = ({name: os.environ[name] for name in ENVIRONMENT if name in os.environ}
                       | {'HOME': str(scratch / 'home'),
                          'CODEX_HOME': os.environ.get('CODEX_HOME') or str(Path.home() / '.codex')})
        ideas = ledger.ideas(runs)
        result = dict(run=run, iteration=n, baseline=base, branch=branch, worktree=str(worktree), logs=str(logs),
                      proposal=None, commit=None, patch_id=None, diff=None, attempts=0, refusals=[])
        for attempt in range(1, loaded['max_proposal_attempts'] + 1):
            result['attempts'] = attempt
            proposal_path = scratch / f'attempt-{attempt}/proposal.json'
            proposal_path.parent.mkdir()
            stdin = logs / f'attempt-{attempt}.prompt.md'
            stdin.write_text(prompt(worktree, branch, base, paths | dict(PROPOSAL=proposal_path), ideas, loaded)
                             + refusal_text(result['refusals']))
            returncode, _ = guard.run_bounded(command(loaded['proposer_command'], repository), worktree, environment,
                                              logs / f'attempt-{attempt}.log', timeout, stdin=stdin)
            try:
                if returncode is None:
                    raise ValueError(f'The proposer ran past {timeout} seconds')
                if returncode:
                    raise ValueError(f'The proposer exited {returncode}')
                return result | check_attempt(worktree, branch, base, proposal_path, paths['PACK'], earlier)
            except ValueError as error:
                result['refusals'].append(dict(attempt=attempt, reason=str(error)))
                guard.git(worktree, 'checkout', '--quiet', '--force', '-B', branch, base)
                guard.git(worktree, 'clean', '-ffdxq')
        return result | dict(reason=f'No proposal: all {result["attempts"]} attempts were refused')
    finally:
        _remove(scratch)


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
    except (OSError, RuntimeError, ValueError) as error:
        print(json.dumps(dict(error=str(error)), indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result['proposal'] else 1


if __name__ == '__main__':
    sys.exit(main())
