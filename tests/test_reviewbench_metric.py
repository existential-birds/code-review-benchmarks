"""Pooled ReviewBench counts through Harbor's actual custom-metric subprocess."""
from pathlib import Path

import pytest

from harbor.metrics.uv_script import UvScript

from benchmarks.reviewbench.metric import compute

SCRIPT = Path(__file__).resolve().parents[1] / 'benchmarks/reviewbench/metric.py'


def counts(candidates, matched, matched_tp, unmatched_tp, golden_tp, covered):
    return dict(candidate_count=candidates, golden_tp_count=golden_tp, matched_count=matched,
                matched_tp_count=matched_tp, unmatched_count=candidates - matched,
                unmatched_tp_count=unmatched_tp, unmatched_fp_count=candidates - matched - unmatched_tp,
                golden_covered_count=covered)


def reward(overall, **strata):
    """A verifier reward as ReviewBench's rewards.ts writes it."""
    groups = {'overall': overall, **{prefix.replace('__', '.'): value for prefix, value in strata.items()}}
    return {'rb_schema': 1, 'rb_task': 1, 'rb_evaluation': 2,
            **{f'{prefix}.{key}': value for prefix, group in groups.items() for key, value in group.items()}}


def test_harbor_metric_pools_scored_trials_and_counts_missing_ones(tmp_path, monkeypatch):
    # A different cwd exercises script imports, not an installed copy of this repo.
    monkeypatch.chdir(tmp_path)
    result = UvScript(SCRIPT).compute([
        reward(counts(4, 2, 2, 1, 4, 2), by_severity__high=counts(1, 1, 1, 0, 2, 1)),
        reward(counts(6, 4, 1, 0, 6, 1), by_severity__high=counts(1, 0, 0, 0, 2, 0)),
        None,
    ])
    assert result['total_trials'] == 3 and result['scored_trials'] == 2 and result['missing_trials'] == 1
    assert result['candidate_count'] == 10 and result['golden_tp_count'] == 10
    # Pooled counts, not an average of per-trial ratios (which would give 0.625).
    assert result['grounded_precision'] == pytest.approx(3 / 6)
    assert result['grounded_recall'] == pytest.approx(3 / 10)
    assert result['augmented_precision'] == pytest.approx(4 / 10)
    assert result['augmented_recall'] == pytest.approx(4 / 11)
    assert result['grounded_f1'] == pytest.approx(2 * .5 * .3 / .8)
    assert result['by_severity.high.grounded_recall'] == pytest.approx(1 / 4)


def test_undefined_ratios_stay_undefined_and_invalid_rewards_fail():
    empty = compute([reward(counts(0, 0, 0, 0, 0, 0))])
    assert empty['scored_trials'] == 1 and empty['candidate_count'] == 0
    assert not {'grounded_precision', 'grounded_recall', 'grounded_f1'} & set(empty)
    assert compute([None]) == {'total_trials': 1, 'scored_trials': 0, 'missing_trials': 1}
    with pytest.raises(ValueError, match='Inconsistent counts'):
        compute([reward(dict(counts(2, 1, 1, 0, 1, 1), matched_tp_count=2))])
    with pytest.raises(ValueError, match='not a ReviewBench reward'):
        compute([{'rb_schema': 2}])
