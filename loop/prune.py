"""Remove what a finished iteration no longer needs, keeping what records it.

    .venv/bin/python -m loop.prune --run <run> [--dry-run]

Run it from the repository root, as ``loop.iterate``; the loop calls ``prune`` between
iterations. The ledger ``loop-runs/<run>/ledger.jsonl`` decides. For every iteration
n rejected or invalid, it removes at once:

- its catalogs, ``artifacts/loop/<run>-<n>-climb/catalog`` and ``-check/catalog``,
  keeping their ``summary.json``;
- its candidate build, ``.cache/daydream-candidates/<commit>/``;
- its worktree, ``.cache/loop/worktrees/<run>-<n>``, when ``loop.iterate`` couldn't
  remove it; the branch stays in the clone;
- the Docker images its trials built and left behind;

and its jobs, the ``jobs/`` folders its summaries list, once ``keep_rejected_jobs``
later iterations are in the ledger. Nothing else is touched: not the ledger, the
patches, the round summaries, the proposer's logs, the evidence pack, the baseline's
builds, catalogs and jobs, an accepted iteration's files, or ``confirmations/``.

Harbor's docker environment at the pinned revision builds a trial's image with
``docker compose build`` in the project named for ``<trial>__env``, made lowercase
with every character outside ``a-z0-9_-`` turned to ``-``; its compose build file names
no image, so Compose names it ``<project>-main``. With the suite's ``delete: true``
Harbor removes it when the trial ends (``down --rmi local``), so the images left are
those of trials that never ended. The build cache is not pruned: ``docker builder
prune`` can't select one catalog's cache, and a candidate's layers sit on layers every
catalog shares.

``--dry-run`` lists what would go and removes nothing. Prints JSON: each item with its
bytes, their total ``bytes`` and ``bytes_freed``. An image's bytes are Docker's size,
which counts the layers it shares with other images. Exits 1 when an item couldn't be
removed, and 2 with an ``error`` when it refuses to run: while an iteration of the run
is in progress, one with a log folder, worktree or rounds and no ledger entry, unless
called by the loop between iterations.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from loop import guard, iterate, ledger, rounds, settings

ROOT = Path(__file__).resolve().parents[1]
# benchmarks.daydream.build's CANDIDATES and loop.propose's folders, under the root.
CANDIDATES = Path('.cache/daydream-candidates')
WORKTREES, LOGS = Path('.cache/loop/worktrees'), Path('.cache/loop/logs')
STAGES = ('climb', 'check')


def docker(*arguments: str) -> subprocess.CompletedProcess:
    """Every Docker command goes through here."""
    return subprocess.run(['docker', *arguments], capture_output=True, text=True, check=False)


def size(path: Path) -> int:
    """The bytes of a file, or of the files under a folder, links counted as themselves."""
    if not path.is_dir() or path.is_symlink():
        return path.lstat().st_size
    return sum((Path(folder) / name).lstat().st_size for folder, _, names in os.walk(path) for name in names)


def image(trial: str) -> str:
    """The image Harbor's docker environment builds for a trial, as the docstring says."""
    project = re.sub(r'[^a-z0-9_-]', '-', f'{trial}__env'.lower())
    return f'{project if re.match("[a-z0-9]", project) else "0" + project}-main'


def started(run: str, numbers: set[int], root: Path) -> list[int]:
    """Iterations of the run with a log folder, worktree or rounds but no ledger entry."""
    pattern = re.compile(rf'{re.escape(run)}-(\d+)(-climb|-check)?')
    found = {int(match[1]) for folder in (root / LOGS, root / WORKTREES, root / rounds.ARTIFACTS) if folder.is_dir()
             for path in folder.iterdir() if (match := pattern.fullmatch(path.name))}
    return sorted(found - numbers)


def _jobs(name: str, root: Path) -> list[str]:
    path = root / rounds.ARTIFACTS / name / 'summary.json'
    return [entry['job'] for entry in json.loads(path.read_text())['jobs']] if path.exists() else []


def _images(wanted: list[tuple[int, str]]) -> list[dict]:
    """The wanted images Docker has, with its size for each."""
    if not wanted:
        return []
    listing = docker('image', 'ls', '--format', '{{.Repository}}')
    if listing.returncode:
        raise RuntimeError(f'docker image ls failed: {listing.stderr.strip()}')
    found = [(n, name) for n, name in wanted if name in set(listing.stdout.split())]
    if not found:
        return []
    sizes = docker('image', 'inspect', '--format', '{{.Size}}', *(name for _, name in found))
    if sizes.returncode:
        raise RuntimeError(f'docker image inspect failed: {sizes.stderr.strip()}')
    return [dict(iteration=n, kind='image', image=name, bytes=int(bytes_))
            for (n, name), bytes_ in zip(found, sizes.stdout.split(), strict=True)]


def _remove(item: dict, root: Path) -> None:
    if item['kind'] == 'image':
        # Harbor removes a trial's image when the trial stops; one left behind belongs to a trial whose Harbor
        # died, and its Compose project's containers still hold it. Take the project down, then the image.
        project = item['image'].removesuffix('-main')
        docker('compose', '--project-name', project, 'down', '--rmi', 'local', '--volumes', '--remove-orphans')
        if item['image'] in docker('image', 'ls', '--format', '{{.Repository}}').stdout.split():
            result = docker('image', 'rm', item['image'])
            if result.returncode:
                raise RuntimeError(f'docker image rm failed: {result.stderr.strip()}')
    elif item['kind'] == 'worktree':
        guard.git(root / iterate.CLONE, 'worktree', 'remove', '--force', str(root / item['path']))
    else:
        shutil.rmtree(root / item['path'])


def prune(run: str, loaded: settings.Settings, *, dry_run: bool = False, between_iterations: bool = False,
          root: Path = ROOT) -> dict:
    """Remove, or with dry_run list, what the run's rejected and invalid iterations no longer need."""
    entries = ledger.read(root / 'loop-runs' / run)
    if not entries:
        raise ValueError(f'No ledger at {root / "loop-runs" / run / "ledger.jsonl"}')
    iterations = [entry for entry in entries if entry['kind'] == 'iteration']
    numbers = {entry['iteration'] for entry in iterations}
    if not between_iterations and (running := started(run, numbers, root)):
        raise RuntimeError(f'Iteration {running[0]} of {run} has started and has no ledger entry: wait for it '
                           'to finish, or record it if it died')
    paths, wanted = [], []
    for entry in iterations:
        n = entry['iteration']
        if entry['verdict']['verdict'] == 'accepted-on-dev':
            continue
        names = [f'{run}-{n}-{stage}' for stage in STAGES]
        jobs = [root / rounds.JOBS / job for name in names for job in _jobs(name, root)]
        paths += [(n, 'catalog', root / rounds.ARTIFACTS / name / 'catalog') for name in names]
        commit = entry.get('candidate', {}).get('commit')
        if commit and commit != entries[0]['baseline_commit']:
            paths.append((n, 'candidate build', root / CANDIDATES / commit))
        paths.append((n, 'worktree', root / WORKTREES / f'{run}-{n}'))
        wanted += [(n, image(trial.name)) for job in jobs if job.is_dir() for trial in sorted(job.iterdir())
                   if trial.is_dir()]
        if sum(later > n for later in numbers) >= loaded['keep_rejected_jobs']:
            paths += [(n, 'job', job) for job in jobs]
    # Reported relative to the root; removed at root / path, whatever the working directory.
    items = [dict(iteration=n, kind=kind, path=str(path.relative_to(root)), bytes=size(path))
             for n, kind, path in paths if path.exists() or path.is_symlink()]
    # The images first, since their names come from the jobs' trial folders. Docker failing to list them
    # leaves one error behind, not every other item.
    try:
        images = _images(wanted)
    except RuntimeError as error:
        images = [dict(iteration=None, kind='image', image=None, bytes=0, error=str(error))]
    items = images + items
    if not dry_run:
        for item in items:
            if 'error' in item:
                continue
            try:
                _remove(item, root)
            except (OSError, RuntimeError) as error:
                item['error'] = str(error)
    total = sum(item['bytes'] for item in items)
    return dict(run=run, dry_run=dry_run, items=items, bytes=total,
                bytes_freed=0 if dry_run else sum(item['bytes'] for item in items if 'error' not in item))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', required=True, type=ledger._run_name, help='The run, a folder name under loop-runs/')
    parser.add_argument('--dry-run', action='store_true', help='List what would go and its size; remove nothing')
    arguments = parser.parse_args(argv)
    try:
        # The run's own settings, as it recorded them at its start.
        entries = ledger.read(ROOT / 'loop-runs' / arguments.run)
        recorded = (entries[0].get('settings', {}) if entries else {})
        loaded = settings.load(**{key: value for key, value in recorded.items()
                                  if key in settings.SETTINGS or key in settings.DERIVED})
        result = prune(arguments.run, loaded, dry_run=arguments.dry_run, root=ROOT)
    except (OSError, RuntimeError, ValueError) as error:
        print(json.dumps(dict(error=str(error)), indent=2))
        return 2
    print(json.dumps(result, indent=2))
    return 1 if any('error' in item for item in result['items']) else 0


if __name__ == '__main__':
    sys.exit(main())
