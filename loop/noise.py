"""Derive the loop's noise settings from rounds of one build and rounds of the oracle.

    .venv/bin/python -m loop.noise --reviewer artifacts/loop/<reviewer>/summary.json \\
      --oracle artifacts/loop/<oracle>/summary.json > loop/noise.json

Both summaries come from ``loop.rounds`` on the climb set: the pinned build's rounds
measure the reviewer's noise, and the oracle's rounds (each task's own true findings)
the judge's alone. Five rounds estimate a standard deviation loosely, so every SD here
is also given at its one-sided 80% upper bound, ``s * sqrt((n - 1) / chi2_0.20(n - 1))``,
and the loop uses that cautious figure:

- ``sigma``: the upper-bound SD between rounds of pooled grounded F1.
- ``accept_threshold(R) = accept_z * sigma * sqrt(2 / R)``: the gain two R-round means
  of one build reach by chance about 1 time in 40, for R from 1 to ``rounds_max``.
- ``rounds``: the smallest R whose threshold is at or below ``target_gain``.
- ``climb_set_size``: 25 if the first 25 climb cases reach ``target_gain`` within
  ``rounds_max``, else 50 if the 50 do, else none, with the smallest detectable gain.
- Guard margins, each ``accept_z`` times the upper-bound SD of a difference of two
  R-round means: pooled precision (the wider of grounded and augmented), unmatched
  findings judged wrong per scored case, and unfinished reviews (failed, incomplete or
  missing) per round.
- The canary's per-case reference counts from the oracle rounds, and a tolerance from
  how far the judge moved a case's counts between those rounds.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics

from benchmarks.reviewbench.metric import compute
from loop import settings

METRICS = ('grounded_precision', 'grounded_recall', 'grounded_f1',
           'augmented_precision', 'augmented_recall', 'augmented_f1')
CANARY_COUNTS = ('matched_count', 'matched_tp_count', 'unmatched_tp_count', 'golden_covered_count')


def chi2_lower(probability: float, df: int) -> float:
    """The chi-square quantile with ``probability`` below it, by bisection on its CDF."""
    def cdf(x: float) -> float:
        # The regularized lower incomplete gamma P(df/2, x/2), by its power series.
        a, z = df / 2, x / 2
        term = total = 1 / a
        for n in range(1, 500):
            term *= z / (a + n)
            total += term
        return total * math.exp(-z + a * math.log(z) - math.lgamma(a))
    low, high = 1e-12, 100.0 * df
    for _ in range(200):
        middle = (low + high) / 2
        low, high = (middle, high) if cdf(middle) < probability else (low, middle)
    return (low + high) / 2


def spread(values: list[float]) -> dict:
    """Mean, sample SD and its one-sided 80% upper bound."""
    n = len(values)
    if n < 2:
        return dict(n=n, mean=statistics.fmean(values) if values else None, sd=None, sd_upper=None)
    sd = statistics.stdev(values)
    return dict(n=n, mean=statistics.fmean(values), sd=sd, sd_upper=sd * math.sqrt((n - 1) / chi2_lower(0.2, n - 1)))


def pooled(summary: dict, cases: list[str]) -> list[dict]:
    """Each round's pooled ratios over ``cases`` only, from the per-case counts."""
    rounds = []
    for number in range(len(summary['rounds'])):
        rewards = [None if summary['cases'][case][number] is None else
                   {'rb_schema': 1, **{f'overall.{key}': value for key, value in summary['cases'][case][number].items()}}
                   for case in cases]
        result = compute(rewards)
        scored = result['scored_trials']
        rounds.append({key: result.get(key) for key in METRICS} | dict(
            scored=scored, wrong_per_case=result['unmatched_fp_count'] / scored if scored else None,
            unfinished=len(cases) - scored))
    return rounds


GUARDS = ('wrong_per_case', 'unfinished')


def block(rounds: list[dict]) -> dict:
    return {key: spread([entry[key] for entry in rounds if entry.get(key) is not None])
            for key in (*METRICS, *GUARDS) if any(key in entry for entry in rounds)}


def derive(reviewer: dict, oracle: dict, loaded: settings.Settings) -> dict:
    climb = list(reviewer['cases'])
    matched = [case for case in climb if all(counts is not None for counts in reviewer['cases'][case])]
    rounds = [{key: entry[key] for key in (*METRICS, 'unmatched_judged_wrong', 'incomplete', 'failed', 'missing',
                                            'evaluated', 'expected', 'cost_usd', 'elapsed_seconds')}
              | dict(wrong_per_case=entry['unmatched_judged_wrong'] / entry['evaluated'] if entry['evaluated'] else None,
                     unfinished=entry['failed'] + entry['incomplete'] + entry['missing'])
              for entry in reviewer['rounds']]
    blocks = {'all': block(rounds), 'matched': block(pooled(reviewer, matched)),
              'first_25': block(pooled(reviewer, climb[:25]))}
    z, target, most = loaded['accept_z'], loaded['target_gain'], loaded['rounds_max']

    def threshold(sd: float, r: int) -> float:
        return z * sd * math.sqrt(2 / r)

    def enough(sd: float) -> int | None:
        return next((r for r in range(1, most + 1) if threshold(sd, r) <= target), None)

    sizes = {'25': blocks['first_25']['grounded_f1']['sd_upper'], '50': blocks['all']['grounded_f1']['sd_upper']}
    size = next((int(name) for name, sd in sizes.items() if enough(sd)), None)
    sigma = sizes[str(size)] if size else sizes['50']
    chosen = enough(sigma) or most
    source = blocks['first_25'] if size == 25 else blocks['all']

    def margin(key: str) -> float | None:
        sd = source.get(key, {}).get('sd_upper')
        return None if sd is None else threshold(sd, chosen)

    # The judge alone: the oracle's pooled scores, and how each case's counts moved between rounds.
    oracle_cases = oracle['cases']
    moved = sorted(case for case, counts in oracle_cases.items()
                   if len({json.dumps(entry, sort_keys=True) for entry in counts if entry is not None}) > 1)
    canary = climb[:loaded['canary_cases']]
    deviations = [abs(entry[key] - statistics.fmean(x[key] for x in oracle_cases[case] if x is not None))
                  for case in canary for entry in oracle_cases[case] if entry is not None for key in CANARY_COUNTS]
    return dict(
        build=reviewer['side'], catalog=reviewer['catalog_identity'], cases=len(climb),
        reviewer=dict(rounds=rounds, blocks=blocks),
        judge=dict(rounds=[{key: entry[key] for key in (*METRICS, 'evaluated', 'expected')} for entry in oracle['rounds']],
                   block=block(oracle['rounds']), cases_scored_differently=moved),
        derived=dict(
            sigma=sigma, rounds=chosen, climb_set_size=size,
            smallest_detectable_gain=None if size else threshold(sizes['50'], most),
            accept_threshold={str(r): threshold(sigma, r) for r in range(1, most + 1)},
            accept_threshold_by_size={name: {str(r): threshold(sd, r) for r in range(1, most + 1)}
                                      for name, sd in sizes.items()},
            # One margin for both precisions: the wider, so neither guard fires on noise alone.
            precision_margin=max(margin('grounded_precision'), margin('augmented_precision')),
            wrong_findings_margin=margin('wrong_per_case'), incomplete_margin=margin('unfinished')),
        canary=dict(cases=canary, counts=list(CANARY_COUNTS),
                    reference={case: {key: statistics.fmean(x[key] for x in oracle_cases[case] if x is not None)
                                      for key in CANARY_COUNTS} for case in canary
                               if any(x is not None for x in oracle_cases[case])},
                    # A case's count may move by this much before the judge counts as drifted.
                    tolerance=max(1.0, z * max(deviations, default=0.0))))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--reviewer', required=True, type=Path)
    parser.add_argument('--oracle', required=True, type=Path)
    arguments = parser.parse_args()
    result = derive(json.loads(arguments.reviewer.read_text()), json.loads(arguments.oracle.read_text()),
                    settings.load())
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
