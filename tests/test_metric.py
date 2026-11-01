"""Pooled AACR counts through Harbor's actual custom-metric subprocess."""
from pathlib import Path

import pytest

from harbor.metrics.uv_script import UvScript

from benchmarks.aacr.metric import compute


SCRIPT = Path(__file__).resolve().parents[1] / 'benchmarks/aacr/metric.py'


def reward(expected, generated, matched_generated, matched_references):
    return {
        'expected_notes': expected,
        'generated_notes': generated,
        'matched_generated_notes': matched_generated,
        'matched_file_semantic_notes': matched_references,
    }


def test_native_harbor_metric_pools_unequal_denominators(tmp_path, monkeypatch):
    # A different cwd exercises script imports, not an installed copy of this repo.
    monkeypatch.chdir(tmp_path)
    result = UvScript(SCRIPT).compute([
        dict(reward(2, 1, 1, 2), balanced_score=1),
        dict(reward(8, 9, 1, 1), balanced_score=1),
        None,
    ])

    assert result == {
        'total_instances': 3,
        'candidate_instances': 2,
        'evaluated_instances': 2,
        'missing_instances': 1,
        'expected_notes': 10,
        'generated_notes': 10,
        'matched_generated_notes': 2,
        'matched_file_semantic_notes': 3,
        'file_semantic_match_rate': .2,
        'file_semantic_recall_rate': .3,
        'balanced_score': .24,
    }


def test_complete_empty_review_is_eligible_and_null_is_missing():
    result = compute([reward(3, 0, 0, 0), None])

    assert result['total_instances'] == 2
    assert result['candidate_instances'] == result['evaluated_instances'] == 1
    assert result['missing_instances'] == 1
    assert result['expected_notes'] == 3
    assert result['generated_notes'] == 0
    assert result['file_semantic_match_rate'] == result['file_semantic_recall_rate'] == 0
    assert result['balanced_score'] == 0


@pytest.mark.parametrize('rewards,missing', [([], 0), ([None, None], 2)])
def test_no_scored_reviews_has_zero_counts(rewards, missing):
    result = compute(rewards)

    assert result['total_instances'] == missing
    assert result['evaluated_instances'] == 0
    assert result['missing_instances'] == missing
    assert result['expected_notes'] == result['generated_notes'] == 0
    assert result['matched_generated_notes'] == result['matched_file_semantic_notes'] == 0
    assert result['balanced_score'] == 0


def test_balanced_score_preserves_upstream_rounding_of_rates():
    result = compute([reward(1, 12, 1, 1)])

    assert result['file_semantic_match_rate'] == .083
    assert result['file_semantic_recall_rate'] == 1
    assert result['balanced_score'] == .153  # Unrounded input rates give .154.


@pytest.mark.parametrize('key', [
    'expected_notes', 'generated_notes', 'matched_generated_notes',
    'matched_file_semantic_notes',
])
def test_missing_counts_are_rejected(key):
    incomplete = reward(2, 1, 1, 2)
    del incomplete[key]

    with pytest.raises(ValueError, match=key):
        compute([incomplete])


@pytest.mark.parametrize('invalid', [True, -1, 1.0, .5, '1', None, float('nan'), float('inf')])
def test_count_values_must_be_nonnegative_integers(invalid):
    with pytest.raises(ValueError, match='expected_notes'):
        compute([dict(reward(2, 1, 1, 2), expected_notes=invalid)])


@pytest.mark.parametrize('invalid', [[], 2, True, 'reward'])
def test_nonobject_rewards_are_rejected(invalid):
    with pytest.raises(ValueError, match='reward'):
        compute([invalid])


@pytest.mark.parametrize('counts', [
    (3, 1, 2, 3),  # more new generated hits than generated findings
    (2, 2, 1, 3),  # more covered references than reference findings
    (3, 3, 2, 1),  # each new generated hit must cover a new reference
    (3, 3, 0, 1),  # covered references require a generated hit
])
def test_impossible_match_counts_are_rejected(counts):
    with pytest.raises(ValueError, match='match counts'):
        compute([reward(*counts)])


def test_native_harbor_metric_rejects_invalid_payload():
    with pytest.raises(RuntimeError, match='expected_notes'):
        UvScript(SCRIPT).compute([dict(reward(2, 1, 1, 2), expected_notes=-1)])
