"""Choose the climb and check sets: fixed ReviewBench dev cases the loop scores.

The dev cases are the tasks `--split dev` converts: ReviewBench's full set less its
test set, as its own exporter writes them. They are sorted by the SHA-256 of
``loop:`` followed by the name; the climb set takes the first ``climb_set_size`` and
the check set the next ``check_set_size``. The order is fixed, so a smaller set is
always the start of a larger one.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import tempfile

from benchmarks.reviewbench import convert
from loop import settings

PACKAGE = Path(__file__).resolve().parent
CLIMB, CHECK = PACKAGE / 'climb.txt', PACKAGE / 'check.txt'
HEADERS = {
    CLIMB: ('# Loop climb set: ReviewBench dev cases scored in every iteration, and the only\n'
            '# cases whose evidence the proposer reads.\n'),
    CHECK: ('# Loop check set: ReviewBench dev cases scored only for a change that passed on\n'
            '# the climb set. The proposer never reads their evidence.\n'),
}
RULE = ('# Chosen from the dev cases of ReviewBench {revision} (its full set less its test\n'
        '# set, as `--split dev` converts them): sort the names by the SHA-256 hex digest of\n'
        '# the UTF-8 text "loop:" followed by the name, then take the first climb_set_size\n'
        '# for the climb set and the next check_set_size for the check set.\n'
        '# Listed in that order. Regenerate with `python -m loop.sets --upstream <ReviewBench>`.\n')


def order(names) -> list[str]:
    return sorted(names, key=lambda name: hashlib.sha256(f'loop:{name}'.encode()).hexdigest())


def dev_cases(upstream: Path) -> list[str]:
    """The task names ReviewBench's exporter writes for its full set and not its test set."""
    with tempfile.TemporaryDirectory(prefix='loop-sets-') as temporary:
        exported = {}
        for split in ('full', 'test'):
            convert.export(Path(upstream).resolve(), Path(temporary) / split, split)
            exported[split] = {path.name for path in (Path(temporary) / split).iterdir() if path.is_dir()}
    return sorted(exported['full'] - exported['test'])


def select(dev: list[str], climb_size: int, check_size: int) -> tuple[list[str], list[str]]:
    if min(climb_size, check_size) < 0:
        raise ValueError('Set sizes cannot be negative')
    if climb_size + check_size > len(dev):
        raise ValueError(f'{climb_size} climb and {check_size} check cases exceed the {len(dev)} dev cases')
    ordered = order(dev)
    return ordered[:climb_size], ordered[climb_size:climb_size + check_size]


def read(path: Path) -> list[str]:
    lines = (line.strip() for line in Path(path).read_text().splitlines())
    return [line for line in lines if line and not line.startswith('#')]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', required=True, type=Path, help='Clean checkout of the pinned ReviewBench source')
    arguments = parser.parse_args()
    convert.check_source(arguments.upstream.resolve())
    loaded = settings.load()
    sets = select(dev_cases(arguments.upstream), loaded['climb_set_size'], loaded['check_set_size'])
    rule = RULE.format(revision=convert.REVIEWBENCH_REVISION[:7])
    for path, names in zip((CLIMB, CHECK), sets):
        path.write_text(HEADERS[path] + rule + ''.join(f'{name}\n' for name in names))
        print(f'{len(names)} cases in {path}')


if __name__ == '__main__':
    main()
