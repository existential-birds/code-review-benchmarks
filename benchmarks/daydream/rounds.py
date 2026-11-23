"""Read Daydream's Harbor jobs as rounds, and check the sides a round report compares.

Every benchmark's round report shares this: a round is one job, a side is named from
the Daydream commit its trials recorded, and each side needs one build of its own.
The benchmark gives only how a scored trial's reward reads as ratios. ``spread`` gives
a score's mean and sample standard deviation over rounds, for the AACR report and
``loop.rounds``; ReviewBench's report keeps ReviewBench's own.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
import json
from pathlib import Path
import statistics


def read_trial(trial: Path, ratios: Callable[[dict], dict]) -> dict:
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
    # separate environment, or a step failed. Every round report applies it.
    failed = (exception or not result.get('finished_at') or result.get('verifier_environment_mode') != 'separate'
              or any(step.get('exception_info') for step in result.get('step_results') or []))
    reward = None if failed else (result.get('verifier_result') or {}).get('rewards')
    return dict(
        task=Path(result['task_id']['path']).name, trial=result['trial_name'],
        status=execution.get('status', 'failed'), exception=exception, reward=reward,
        ratios=None if reward is None else ratios(reward),
        omitted_findings=execution.get('omitted_findings', 0), elapsed_seconds=execution.get('elapsed_seconds'),
        usage=execution.get('usage') or {},
        side=execution['build'].get('source_commit') if execution.get('build') else None)


def read_round(job: Path, expected: Mapping, ratios: Callable[[dict], dict]) -> dict:
    """One job as a round: its trials and their counts, and Harbor's own totals as it wrote them."""
    stats = json.loads((job / 'result.json').read_text())['stats']
    trials = sorted((read_trial(path.parent, ratios) for path in job.glob('*/result.json')),
                    key=lambda trial: trial['task'])
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
        elapsed_seconds=sum(trial['elapsed_seconds'] or 0 for trial in trials),
        # An API-equivalent estimate, not a charge, summed over the trials that reported one.
        cost_usd=sum(costs) if costs else None, cost_trials=len(costs),
        harbor={**{key: stats[key] for key in ('n_completed_trials', 'n_errored_trials', 'n_cancelled_trials',
                                               'n_input_tokens', 'n_cache_tokens', 'n_output_tokens')},
                'metrics': [metric for group in stats['evals'].values() for metric in group['metrics']]},
        trials=trials)


def builds(entries: list[dict]) -> set:
    """The Daydream builds a side's trials recorded. A trial without a review record names none:
    it may fail in any side, but a scored one adds None, which no side may hold."""
    return {trial['side'] for entry in entries for trial in entry['trials']
            if trial['side'] is not None or trial['reward'] is not None}


def read_sides(sides: list[list[Path]], expected: Mapping, ratios: Callable[[dict], dict]) -> dict[str, list[dict]]:
    """Every side's rounds, keyed by its build; refuses a job listed twice and a side without one build of its own."""
    paths = [job.resolve() for jobs in sides for job in jobs]
    if len(set(paths)) != len(paths):
        raise ValueError('A job is one round of one side, but one is listed more than once')
    rounds = {}
    for jobs in sides:
        entries = [read_round(job, expected, ratios) for job in jobs]
        identity = builds(entries)
        if len(identity) != 1 or None in identity or identity & rounds.keys():
            raise ValueError(f'Each side needs one Daydream build of its own, not {sorted(map(str, identity))}')
        rounds[identity.pop()] = entries
    return rounds


def matched(rounds: dict[str, list[dict]], expected: Mapping) -> set[str]:
    """The review cases every job of every side scored."""
    return set(expected).intersection(*({trial['task'] for trial in entry['trials'] if trial['reward'] is not None}
                                        for entries in rounds.values() for entry in entries))


def spread(values: list[float | None]) -> dict:
    """The mean and sample standard deviation (n-1) over the rounds that define a score."""
    values = [value for value in values if value is not None]
    return dict(defined_rounds=len(values), mean=statistics.fmean(values) if values else None,
                stddev=statistics.stdev(values) if len(values) > 1 else None)
