# /// script
# requires-python = ">=3.11"
# ///
"""Pool scored ReviewBench rewards into one metric; Harbor runs this as a uv script.

ReviewBench's own metric withholds every score unless all planned trials scored.
This pools the counts of the trials that did, using its unchanged count checks and
ratios, so read scores alongside ``missing_trials``. A null reward is a trial that
produced no score.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# uv runs this file in its own environment, from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from benchmarks.reviewbench.upstream.metric import COUNTS, ratios, read_counts


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def compute(rewards: list[dict | None]) -> dict[str, int | float]:
    output = {'total_trials': len(rewards), 'scored_trials': 0, 'missing_trials': 0}
    pooled: dict[str, dict[str, int]] = {}
    for index, reward in enumerate(rewards, start=1):
        if reward is None:
            output['missing_trials'] += 1
            continue
        if not isinstance(reward, dict) or reward.get('rb_schema') != 1:
            raise ValueError(f'reward {index} is not a ReviewBench reward')
        output['scored_trials'] += 1
        for prefix, counts in read_counts(reward).items():
            group = pooled.setdefault(prefix, dict.fromkeys(COUNTS, 0))
            for key in COUNTS:
                group[key] += counts[key]
    if 'overall' in pooled:
        overall = {**pooled['overall'], **ratios(pooled['overall'])}
        overall['grounded_f1'] = _f1(overall['grounded_precision'], overall['grounded_recall'])
        overall['augmented_f1'] = _f1(overall['augmented_precision'], overall['augmented_recall'])
        output.update(overall)
    # Severity is the one breakdown reported here; every trial's reward keeps the rest.
    for prefix in sorted(prefix for prefix in pooled if prefix.startswith('by_severity.')):
        output.update({f'{prefix}.{key}': value for key, value in ratios(pooled[prefix]).items()})
    # Undefined ratios stay undefined rather than becoming zero.
    return {key: value for key, value in output.items() if value is not None}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-i', '--input-path', type=Path, required=True)
    parser.add_argument('-o', '--output-path', type=Path, required=True)
    args = parser.parse_args()
    rewards = [json.loads(line) for line in args.input_path.read_text().splitlines()]
    args.output_path.write_text(json.dumps(compute(rewards)) + '\n')
