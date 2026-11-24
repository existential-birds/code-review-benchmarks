"""Decide whether a candidate build beats the baseline, from their round summaries (#44).

    .venv/bin/python -m loop.decide --baseline <summary.json> --candidate <summary.json> --stage climb|check

prints JSON: ``verdict`` (accept, reject, continue or invalid), ``reasons`` and every
number used, and exits 0; it exits 2 with an ``error`` when the two can't be compared.
Every limit comes from ``loop/settings.toml``.

Like is compared with like. Only the review cases every round of both sides scored
count, and each side's scores are pooled again per round over those cases alone, from
the summary's per-case counts. A build can't raise its score by failing on hard cases:
too few matched cases is ``invalid``, and failed or incomplete reviews are a guard.
Two summaries of another ReviewBench revision, split or judge, or of other cases, are
refused; the Daydream build differs by design.

The gain is the candidate's mean pooled grounded F1 minus the baseline's. The climb
stage accepts only a gain above ``accept_threshold``; the check stage, above zero.
Grounded precision divides by matched findings only, so a flood of findings raises
recall and F1 for free. Each guard is a reject reason of its own: grounded or augmented
precision lower by more than ``precision_margin``; unmatched findings judged wrong, per
case, higher by more than ``wrong_findings_margin``; findings per case above
``max_findings_ratio`` times the baseline's; failed or incomplete reviews per round
higher by more than ``incomplete_margin``; cost or time per case above
``max_cost_ratio`` times the baseline's.

With fewer candidate rounds than the planned ``rounds``, it never accepts. Each
remaining round is assumed to come out as well as noise plausibly allows: the gain so
far plus ``accept_z`` standard deviations of a one-round difference, ``sigma * sqrt(2)``.
If the final mean would still miss what acceptance needs, it rejects now; otherwise
it continues. ``futility_stop = false`` always continues.

``matched_cases``, ``side_rounds`` and ``means`` are the comparison arithmetic that
confirmation (#54) reuses. ``self_check`` decides a build against itself.
"""
from __future__ import annotations

import argparse
from collections import Counter
from itertools import combinations
import json
import math
from pathlib import Path
import statistics
import sys

from benchmarks.reviewbench.metric import compute
from loop import settings
from loop.rounds import METRICS

STAGES = ('climb', 'check')
# What makes two summaries' scores comparable; the Daydream build is what differs.
IDENTITY = ('reviewbench_revision', 'split', 'judge')


def matched_cases(baseline: dict, candidate: dict) -> list[str]:
    """The review cases every round of both sides scored, once the two are known comparable."""
    for key in IDENTITY:
        if baseline['catalog_identity'][key] != candidate['catalog_identity'][key]:
            raise ValueError(f'Refusing to compare summaries of different {key}: '
                             f'{baseline["catalog_identity"][key]!r} and {candidate["catalog_identity"][key]!r}')
    if set(baseline['cases']) != set(candidate['cases']):
        raise ValueError('Refusing to compare summaries over different review cases')
    for summary in (baseline, candidate):
        if not summary.get('rounds') or any(len(counts) != len(summary['rounds'])
                                            for counts in summary['cases'].values()):
            raise ValueError('Each summary needs at least one round, and every case counted in each')
    return sorted(case for case in baseline['cases']
                  if all(counts is not None for side in (baseline, candidate) for counts in side['cases'][case]))


def _per(total: float | None, count: int) -> float | None:
    return total / count if total is not None and count else None


def side_rounds(summary: dict, cases: list[str]) -> list[dict]:
    """Each round's scores pooled over ``cases`` alone, with its findings, failures, cost and time per case."""
    if not cases:
        raise ValueError('No review cases to pool')
    result = []
    for number, entry in enumerate(summary['rounds']):
        pooled = compute([{'rb_schema': 1, **{f'overall.{key}': value
                                              for key, value in summary['cases'][case][number].items()}}
                          for case in cases])
        result.append(dict(
            **{key: pooled.get(key) for key in METRICS},
            findings_per_case=pooled['candidate_count'] / len(cases),
            wrong_findings_per_case=pooled['unmatched_fp_count'] / len(cases),
            # Over the whole round, since a review that failed has left the matched cases.
            # Every review that didn't finish: failed, incomplete or missing from the job.
            unfinished=entry['failed'] + entry['incomplete'] + entry['missing'],
            # The summary keeps cost and time as round totals only, so per case is the total
            # over the trials it covers: cost_usd over cost_trials, the trials that reported a
            # cost, and elapsed_seconds, summed over every trial in the job, over those trials.
            cost_per_case=_per(entry['cost_usd'], entry['cost_trials']),
            seconds_per_case=_per(entry['elapsed_seconds'], entry['expected'] - entry['missing'])))
    return result


def means(rounds: list[dict]) -> dict:
    """Each figure's mean over the rounds; None unless every round defines it."""
    return {key: None if any(entry[key] is None for entry in rounds) else statistics.fmean(
        entry[key] for entry in rounds) for key in rounds[0]}


def decide(baseline: dict, candidate: dict, stage: str, limits: settings.Settings, *,
           rounds: int | None = None) -> dict:
    """The verdict on a candidate's summary against the baseline's, its reasons and every number used.

    ``rounds`` is the planned number of rounds per side, the ``rounds`` setting unless given.
    """
    if stage not in STAGES:
        raise ValueError(f'Stage must be one of {STAGES}, not {stage!r}')
    planned = rounds or limits['rounds']
    matched = matched_cases(baseline, candidate)
    done = len(candidate['rounds'])
    if len(baseline['rounds']) != planned or done > planned:
        raise ValueError(f'The baseline needs {planned} rounds and the candidate at most {planned}, '
                         f'not {len(baseline["rounds"])} and {done}')
    total, least = len(baseline['cases']), limits['min_matched_fraction']
    result = dict(stage=stage, rounds=dict(planned=planned, baseline=planned, candidate=done),
                  matched=dict(cases=len(matched), of=total, fraction=len(matched) / total, min_fraction=least))

    def verdict(word: str, *reasons: str) -> dict:
        return dict(verdict=word, reasons=list(reasons), **result)

    if not matched or len(matched) / total < least:
        return verdict('invalid', f'Only {len(matched)} of {total} cases were scored in every round of both '
                                  f'sides, below the {least:g} needed to compare them')
    sides = dict(baseline=side_rounds(baseline, matched), candidate=side_rounds(candidate, matched))
    for name, entries in sides.items():
        result[name] = dict(rounds=entries, mean=means(entries))
    base, cand = result['baseline']['mean'], result['candidate']['mean']
    # A candidate that matched no finding on the matched cases in some round has no precision,
    # and so no F1, where the baseline has them: a worse review, not a failed comparison.
    unmatched = sorted(key for key in METRICS if base[key] is not None and cand[key] is None)
    if unmatched:
        return verdict('reject', f'The candidate matched no findings in some round, leaving undefined: '
                                 f'{", ".join(unmatched)}')
    undefined = sorted(key for key in base if base[key] is None or cand[key] is None)
    if undefined:
        return verdict('invalid', f'Undefined in some round on the matched cases: {", ".join(undefined)}')
    # Every gain is reported, so the ledger shows whether recall or precision moved.
    result['gain'] = {key: cand[key] - base[key] for key in METRICS}
    gain = result['gain']['grounded_f1']
    needed = limits.accept_threshold(planned) if stage == 'climb' else 0.0
    result.update(needed_gain=needed, sigma=limits['sigma'], accept_z=limits['accept_z'])

    if done < planned:
        if not limits['futility_stop']:
            return verdict('continue', f'{done} of {planned} candidate rounds scored')
        one_round = limits['sigma'] * math.sqrt(2)
        best = gain + (planned - done) / planned * limits['accept_z'] * one_round
        result['early_stop'] = dict(one_round_sd=one_round, best_final_gain=best)
        if best <= needed:
            return verdict('reject', f'After {done} of {planned} rounds the grounded F1 gain is {gain:.4g}; even at '
                                     f'its best plausible the final gain {best:.4g} would not exceed {needed:.4g}')
        return verdict('continue', f'After {done} of {planned} rounds the final gain could still reach '
                                   f'{best:.4g}, above {needed:.4g}')

    precision, cost = limits['precision_margin'], limits['max_cost_ratio']
    # figure: (the candidate's limit from the baseline's mean, whether it is a floor)
    bounds = {
        'grounded_precision': (base['grounded_precision'] - precision, True),
        'augmented_precision': (base['augmented_precision'] - precision, True),
        'wrong_findings_per_case': (base['wrong_findings_per_case'] + limits['wrong_findings_margin'], False),
        'findings_per_case': (base['findings_per_case'] * limits['max_findings_ratio'], False),
        'unfinished': (base['unfinished'] + limits['incomplete_margin'], False),
        'cost_per_case': (base['cost_per_case'] * cost, False),
        'seconds_per_case': (base['seconds_per_case'] * cost, False),
    }
    result['guards'], reasons = {}, []
    if gain <= needed:
        reasons.append(f'The grounded F1 gain {gain:.4g} does not exceed {needed:.4g}')
    for key, (limit, floor) in bounds.items():
        passed = cand[key] >= limit if floor else cand[key] <= limit
        result['guards'][key] = dict(baseline=base[key], candidate=cand[key], limit=limit, passed=passed)
        if not passed:
            reasons.append(f'{key} {cand[key]:.4g} is {"below" if floor else "above"} its limit {limit:.4g} '
                           f'(baseline {base[key]:.4g})')
    if reasons:
        return verdict('reject', *reasons)
    return verdict('accept', f'The grounded F1 gain {gain:.4g} exceeds {needed:.4g} and every guard passed')


def _pick(summary: dict, numbers: tuple[int, ...]) -> dict:
    return {**summary, 'rounds': [summary['rounds'][number] for number in numbers],
            'cases': {case: [counts[number] for number in numbers] for case, counts in summary['cases'].items()}}


def self_check(summary: dict, limits: settings.Settings) -> dict:
    """Decide one build against itself at the climb stage, in every ordered split of its
    rounds into two groups of half of them, and count how often it "beats" itself."""
    count = len(summary['rounds'])
    size = count // 2
    if not size:
        raise ValueError('A self-check needs at least two rounds')
    verdicts, on_gain = Counter(), 0
    for first in combinations(range(count), size):
        rest = tuple(number for number in range(count) if number not in first)
        for second in combinations(rest, size):
            result = decide(_pick(summary, first), _pick(summary, second), 'climb', limits, rounds=size)
            verdicts[result['verdict']] += 1
            # The guards' margins are set for the planned rounds, tighter than these groups need,
            # so also count the splits whose gain alone would pass.
            on_gain += 'gain' in result and result['gain']['grounded_f1'] > result['needed_gain']
    return dict(group_rounds=size, decisions=verdicts.total(), verdicts=dict(verdicts),
                beats_itself=verdicts['accept'], beats_itself_on_gain=on_gain)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--baseline', required=True, type=Path, help="The baseline's summary.json from loop.rounds")
    parser.add_argument('--candidate', required=True, type=Path, help="The candidate's summary.json from loop.rounds")
    parser.add_argument('--stage', required=True, choices=STAGES)
    arguments = parser.parse_args(argv)
    try:
        result = decide(json.loads(arguments.baseline.read_text()), json.loads(arguments.candidate.read_text()),
                        arguments.stage, settings.load())
    except (OSError, LookupError, ValueError) as error:
        # Not a verdict on the change: the two summaries couldn't be compared, or a setting is unset.
        print(json.dumps(dict(error=str(error)), indent=2))
        return 2
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
