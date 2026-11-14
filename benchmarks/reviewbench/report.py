"""Compare reviewer configurations over rounds of one ReviewBench catalog.

Harbor pools repeated attempts (``-k``), so a round is its own job: one complete
pass (``-k 1``) over the catalog. Give each side's jobs after ``--side``, in round
order::

    .venv/bin/python -m benchmarks.reviewbench.report --catalog artifacts/reviewbench/<catalog> \\
      --side jobs/<a-round-1> jobs/<a-round-2> jobs/<a-round-3> \\
      --side jobs/<b-round-1> jobs/<b-round-2> jobs/<b-round-3>

A side is named from what its trials recorded, never from a label: the Daydream
commit and the review it ran. A job is one round of one side, so none is listed
twice. ReviewBench's unchanged ``compute_rounds`` scores each
round and averages the rounds, once for each set of review cases in ``BLOCKS``.
Nothing here ranks the sides or adds a score.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

from benchmarks.reviewbench.upstream.metric import compute_rounds, ratios, read_counts

# The review cases each scored block covers; the report repeats this beside their names.
BLOCKS = {
    'all': "Every review case in the catalog, as ReviewBench scores rounds: one unscored trial "
           "withholds its round's ratios and every average.",
    'matched': 'A reduced set: only the review cases that every job of every side scored.',
}


def read_trial(trial: Path) -> dict:
    """One trial's outcome: Harbor's result and reward, and the review's own record."""
    result = json.loads((trial / 'result.json').read_text())
    try:
        execution = json.loads((trial / 'agent/execution.json').read_text())
    except (OSError, ValueError):
        # A trial that left no record never reached the review.
        execution = {}
    exception = (result.get('exception_info') or {}).get('exception_type')
    # ReviewBench's own rule for a failed trial (``rb_failed`` in its reviewbench_plugin.py), which
    # is not scored whatever reward it left: it raised, never finished, was verified outside a
    # separate environment, or a step failed.
    failed = (exception or not result.get('finished_at') or result.get('verifier_environment_mode') != 'separate'
              or any(step.get('exception_info') for step in result.get('step_results') or []))
    reward = None if failed else (result.get('verifier_result') or {}).get('rewards')
    return dict(
        task=Path(result['task_id']['path']).name, trial=result['trial_name'],
        status=execution.get('status', 'failed'), exception=exception, reward=reward,
        ratios=None if reward is None else ratios(read_counts(reward)['overall']),
        omitted_findings=execution.get('omitted_findings', 0), elapsed_seconds=execution.get('elapsed_seconds'),
        usage=execution.get('usage') or {},
        side='{}/{}'.format(execution['build'].get('source_commit'), execution.get('assignment_policy') or 'review')
        if execution.get('build') else None,
        assignment_policy=(execution.get('committed_review') or {}).get('assignment_policy'))


def read_round(job: Path, expected: dict[str, dict]) -> dict:
    """One job as a round: its trials and their counts, and Harbor's own totals as it wrote them."""
    stats = json.loads((job / 'result.json').read_text())['stats']
    trials = sorted((read_trial(path.parent) for path in job.glob('*/result.json')), key=lambda trial: trial['task'])
    tasks = [trial['task'] for trial in trials]
    if len(set(tasks)) != len(tasks) or not set(tasks) <= set(expected):
        raise ValueError(f'{job} is not one pass (-k 1) over the catalog')
    status = Counter(trial['status'] for trial in trials)
    costs = [trial['usage']['cost_usd'] for trial in trials if 'cost_usd' in trial['usage']]
    return dict(
        job=job.name, expected=len(expected), missing=len(expected) - len(trials), produced=status['complete'],
        completed_empty=status['completed_empty'], incomplete=status['incomplete'],
        failed=len(trials) - status['complete'] - status['completed_empty'] - status['incomplete'],
        evaluated=sum(trial['reward'] is not None for trial in trials),
        conversion_limited=sum(trial['omitted_findings'] > 0 for trial in trials),
        omitted_findings=sum(trial['omitted_findings'] for trial in trials),
        decomposed=sum(bool((trial['assignment_policy'] or {}).get('decomposed')) for trial in trials),
        elapsed_seconds=sum(trial['elapsed_seconds'] or 0 for trial in trials),
        # An API-equivalent estimate, not a charge, summed over the trials that reported one.
        cost_usd=sum(costs) if costs else None, cost_trials=len(costs),
        harbor={**{key: stats[key] for key in ('n_completed_trials', 'n_errored_trials', 'n_cancelled_trials',
                                               'n_input_tokens', 'n_cache_tokens', 'n_output_tokens')},
                'metrics': [metric for group in stats['evals'].values() for metric in group['metrics']]},
        trials=trials)


def score(rounds: list[dict], expected: dict[str, dict], tasks: set[str]) -> dict:
    """ReviewBench's round metric over ``tasks``; a trial without a score is a failed one."""
    if not tasks:
        return {}
    rewards = [None if trial['reward'] is None else {**trial['reward'], 'rb_round': number, 'rb_failed': 0}
               for number, entry in enumerate(rounds, 1) for trial in entry['trials'] if trial['task'] in tasks]
    planned = {str(expected[task]['task_id']): expected[task]['evaluation_id'] for task in tasks}
    return compute_rounds(rewards, planned, len(rounds), len(expected))


def report(catalog: Path, sides: list[list[Path]]) -> dict:
    """Every side's rounds, and ReviewBench's round metric for each block of review cases."""
    listing = json.loads((catalog / 'catalog.json').read_text())
    expected = {name: json.loads((catalog / name / 'tests/task.json').read_text()) for name in listing['tasks']}
    paths = [job.resolve() for jobs in sides for job in jobs]
    if len(set(paths)) != len(paths):
        raise ValueError('A job is one round of one side, but one is listed more than once')
    rounds = {}
    for jobs in sides:
        entries = [read_round(job, expected) for job in jobs]
        # A trial without a review record names no side: it may fail in any side, but not be scored in one.
        identity = {trial['side'] for entry in entries for trial in entry['trials']
                    if trial['side'] is not None or trial['reward'] is not None}
        if len(identity) != 1 or None in identity or identity & rounds.keys():
            raise ValueError(f'Each side needs one Daydream build and review of its own, not {sorted(map(str, identity))}')
        rounds[identity.pop()] = entries
    jobs = [entry['trials'] for entries in rounds.values() for entry in entries]
    matched = set(expected).intersection(*({trial['task'] for trial in job if trial['reward'] is not None}
                                           for job in jobs))
    tasks = {'all': set(expected), 'matched': matched}
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
