"""Compare Daydream builds over rounds of one ReviewBench catalog.

Harbor pools repeated attempts (``-k``), so a round is its own job: one complete
pass (``-k 1``) over the catalog. Give each side's jobs after ``--side``, in round
order::

    .venv/bin/python -m benchmarks.reviewbench.report --catalog artifacts/reviewbench/<catalog> \\
      --side jobs/<a-round-1> jobs/<a-round-2> jobs/<a-round-3> \\
      --side jobs/<b-round-1> jobs/<b-round-2> jobs/<b-round-3>

A side is named from what its trials recorded, never from a label: the Daydream
commit they ran. A job is one round of one side, so none is listed twice. ReviewBench's unchanged ``compute_rounds`` scores each
round and averages the rounds, once for each set of review cases in ``BLOCKS``.
Nothing here ranks the sides or adds a score.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.daydream import rounds as shared
from benchmarks.reviewbench.upstream.metric import compute_rounds, ratios, read_counts

# The review cases each scored block covers; the report repeats this beside their names.
BLOCKS = {
    'all': "Every review case in the catalog, as ReviewBench scores rounds: one unscored trial "
           "withholds its round's ratios and every average.",
    'matched': 'A reduced set: only the review cases that every job of every side scored.',
}


def trial_ratios(reward: dict) -> dict:
    """A scored trial's ratios, from ReviewBench's overall counts."""
    return ratios(read_counts(reward)['overall'])


def read_round(job: Path, expected: dict[str, dict]) -> dict:
    """One job as a round, read as every benchmark's round report reads it."""
    return shared.read_round(job, expected, trial_ratios)


def score(rounds: list[dict], expected: dict[str, dict], tasks: set[str]) -> dict:
    """ReviewBench's round metric over ``tasks``; a trial without a score is a failed one."""
    if not tasks:
        return {}
    rewards = [None if trial['reward'] is None else {**trial['reward'], 'rb_round': number, 'rb_failed': 0}
               for number, entry in enumerate(rounds, 1) for trial in entry['trials'] if trial['task'] in tasks]
    planned = {str(expected[task]['task_id']): expected[task]['evaluation_id'] for task in tasks}
    return compute_rounds(rewards, planned, len(rounds), len(expected))


def read_catalog(catalog: Path) -> tuple[dict, dict[str, dict]]:
    """The catalog's listing, and each review case's planned task and evaluation ids."""
    listing = json.loads((catalog / 'catalog.json').read_text())
    return listing, {name: json.loads((catalog / name / 'tests/task.json').read_text()) for name in listing['tasks']}


def report(catalog: Path, sides: list[list[Path]]) -> dict:
    """Every side's rounds, and ReviewBench's round metric for each block of review cases."""
    listing, expected = read_catalog(catalog)
    rounds = shared.read_sides(sides, expected, trial_ratios)
    tasks = {'all': set(expected), 'matched': shared.matched(rounds, expected)}
    return dict(
        catalog={key: listing[key] for key in ('reviewbench_revision', 'split', 'judge')},
        blocks={name: dict(meaning=meaning, tasks=sorted(tasks[name])) for name, meaning in BLOCKS.items()},
        sides={side: dict(rounds=entries, **{name: score(entries, expected, tasks[name]) for name in BLOCKS})
               for side, entries in rounds.items()})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--catalog', required=True, type=Path,
                        help='The catalog the jobs ran, or one generated with the same review cases')
    parser.add_argument('--side', required=True, action='append', nargs='+', type=Path, metavar='JOB',
                        help="One side's jobs, a round each; repeat for every side")
    arguments = parser.parse_args()
    print(json.dumps(report(arguments.catalog, arguments.side), indent=2, allow_nan=False))
