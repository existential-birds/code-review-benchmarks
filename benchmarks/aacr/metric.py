# /// script
# requires-python = ">=3.11"
# dependencies = ["typing-extensions==4.16.0"]
# ///
"""Pool scored AACR rewards through Harbor's native uv-script interface.

Non-null rewards are scoring-eligible, including completed empty reviews. Harbor
does not pass exception_info here: a later lifecycle error can coexist with a
reward. These instance counters describe scoring inputs; inspect native job and
trial results separately for completion and errors.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# uv executes this file in an isolated dependency environment from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from benchmarks.aacr.upstream import evaluate


COUNTS = (
    'expected_notes', 'generated_notes', 'matched_generated_notes',
    'matched_file_semantic_notes',
)


def compute(rewards: list[dict | None]) -> dict[str, int | float]:
    stats = {
        'total_instances': len(rewards),
        'candidate_instances': 0,
        'evaluated_instances': 0,
        'missing_instances': 0,
        **dict.fromkeys(COUNTS, 0),
    }
    for index, reward in enumerate(rewards, start=1):
        if reward is None:
            stats['missing_instances'] += 1
            continue
        if not isinstance(reward, dict):
            raise ValueError(f'reward {index} must be an object or null')
        for key in COUNTS:
            value = reward.get(key)
            if type(value) is not int or value < 0:
                raise ValueError(f'reward {index}: {key} must be a nonnegative integer')
        expected, generated, matched_generated, matched_references = (
            reward[key] for key in COUNTS
        )
        if (matched_generated > generated or matched_references > expected
                or matched_generated > matched_references
                or bool(matched_generated) != bool(matched_references)):
            raise ValueError(f'reward {index}: impossible AACR match counts')
        stats['candidate_instances'] += 1
        stats['evaluated_instances'] += 1
        for key in COUNTS:
            stats[key] += reward[key]

    summary = evaluate._compute_summary(stats)
    return {key: summary[key] for key in (
        *stats, 'file_semantic_match_rate', 'file_semantic_recall_rate', 'balanced_score',
    )}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-i', '--input-path', type=Path, required=True)
    parser.add_argument('-o', '--output-path', type=Path, required=True)
    args = parser.parse_args()
    rewards = [json.loads(line) for line in args.input_path.read_text().splitlines()]
    args.output_path.write_text(json.dumps(compute(rewards)) + '\n')
