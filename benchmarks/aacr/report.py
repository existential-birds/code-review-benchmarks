"""Compare Daydream builds over rounds of one AACR catalog.

Harbor pools repeated attempts (``-k``), so a round is its own job: one complete
pass (``-k 1``) over the catalog. Give each side's jobs after ``--side``, in round
order::

    .venv/bin/python -m benchmarks.aacr.report --catalog artifacts/aacr/<catalog> \\
      --side jobs/<a-round-1> jobs/<a-round-2> jobs/<a-round-3> \\
      --side jobs/<b-round-1> jobs/<b-round-2> jobs/<b-round-3>

Jobs are read and sides checked as for ReviewBench's report: a side is named from the
Daydream commit its trials recorded, never from a label, and a job is one round of one
side. AACR's unchanged metric (``metric.py``'s ``compute``) pools each round's scored
trials, once for each set of review cases in ``BLOCKS``; the rounds' mean and sample
standard deviation (n-1) follow. Nothing here ranks the sides or adds a score.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.aacr.metric import compute
from benchmarks.daydream import rounds as shared

RATES = ('file_semantic_match_rate', 'file_semantic_recall_rate', 'balanced_score')
# The review cases each scored block covers; the report repeats this beside their names.
BLOCKS = {
    'all': "Every review case in the catalog. Each round pools the trials it scored, as the job's own "
           "metric does; an unscored trial leaves the denominators, so read the scores with the counts.",
    'matched': 'A reduced set: only the review cases that every job of every side scored.',
}


def trial_ratios(reward: dict) -> dict:
    """A scored trial's rates, as AACR's evaluator gave them for its one review case."""
    return {key: reward.get(key) for key in RATES}


def read_round(job: Path, expected: dict[str, dict]) -> dict:
    """One job as a round, read as every benchmark's round report reads it."""
    return shared.read_round(job, expected, trial_ratios)


def read_catalog(catalog: Path) -> tuple[dict, dict[str, dict]]:
    """The catalog's listing, and the review cases it generated; a case that failed to convert has no task."""
    listing = json.loads((catalog / 'catalog.json').read_text())
    return listing, {case['task_name']: case for case in listing['cases'] if case['status'] == 'generated'}


def pool(rewards: list[dict | None]) -> dict:
    """AACR's metric over one round's rewards, null for an unscored trial. With none scored, its rates
    are undefined rather than AACR's 0.0 for an empty denominator."""
    pooled = compute(rewards)
    return pooled if pooled['evaluated_instances'] else {**pooled, **dict.fromkeys(RATES)}


def score(rounds: list[dict], tasks: set[str]) -> dict:
    """Every round's pooled metric over ``tasks``, then each rate's mean and spread over the rounds."""
    if not tasks:
        return {}
    pooled = []
    for entry in rounds:
        rewards = {trial['task']: trial['reward'] for trial in entry['trials']}
        pooled.append(pool([rewards.get(task) for task in sorted(tasks)]))
    return dict(rounds=pooled, summary={key: shared.spread([entry[key] for entry in pooled]) for key in RATES})


def report(catalog: Path, sides: list[list[Path]]) -> dict:
    """Every side's rounds, and AACR's metric for each block of review cases."""
    listing, expected = read_catalog(catalog)
    rounds = shared.read_sides(sides, expected, trial_ratios)
    tasks = {'all': set(expected), 'matched': shared.matched(rounds, expected)}
    return dict(
        catalog={key: listing[key] for key in ('aacr_revision', 'dataset_sha256', 'split')},
        blocks={name: dict(meaning=meaning, tasks=sorted(tasks[name])) for name, meaning in BLOCKS.items()},
        sides={side: dict(rounds=entries, **{name: score(entries, tasks[name]) for name in BLOCKS})
               for side, entries in rounds.items()})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--catalog', required=True, type=Path,
                        help='The catalog the jobs ran, or one generated with the same review cases')
    parser.add_argument('--side', required=True, action='append', nargs='+', type=Path, metavar='JOB',
                        help="One side's jobs, a round each; repeat for every side")
    arguments = parser.parse_args()
    print(json.dumps(report(arguments.catalog, arguments.side), indent=2, allow_nan=False))
