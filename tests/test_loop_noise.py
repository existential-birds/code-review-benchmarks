import math

import pytest

from loop import noise, settings


def counts(matched, matched_tp, covered, golden=10, unmatched_tp=0, unmatched_fp=0):
    return dict(candidate_count=matched + unmatched_tp + unmatched_fp, golden_tp_count=golden, matched_count=matched,
                matched_tp_count=matched_tp, unmatched_count=unmatched_tp + unmatched_fp,
                unmatched_tp_count=unmatched_tp, unmatched_fp_count=unmatched_fp, golden_covered_count=covered)


def summary(case_counts: dict, side='p' * 40):
    """A loop.rounds summary built from per-case counts, one list entry per round."""
    from benchmarks.reviewbench.metric import compute
    rounds_count = len(next(iter(case_counts.values())))
    rounds = []
    for number in range(rounds_count):
        rewards = [None if c[number] is None else {'rb_schema': 1, **{f'overall.{k}': v for k, v in c[number].items()}}
                   for c in case_counts.values()]
        pooled = compute(rewards)
        scored = pooled['scored_trials']
        rounds.append({key: pooled.get(key) for key in noise.METRICS} | dict(
            expected=len(case_counts), missing=0, evaluated=scored, failed=len(case_counts) - scored, incomplete=0,
            unmatched_judged_wrong=pooled.get('unmatched_fp_count'), cost_usd=2.5 * scored, elapsed_seconds=300.0))
    return dict(side=side, catalog_identity={'judge': 'j'}, rounds=rounds, cases=case_counts)


def test_the_upper_bound_widens_a_five_round_sd_by_the_chi_square_factor():
    assert noise.chi2_lower(0.2, 4) == pytest.approx(1.6488, abs=1e-4)
    result = noise.spread([0.40, 0.42, 0.41, 0.39, 0.43])
    assert result['sd'] == pytest.approx(0.015811, abs=1e-6)
    assert result['sd_upper'] == pytest.approx(result['sd'] * math.sqrt(4 / 1.648777), rel=1e-5)


def test_settings_follow_from_the_rounds_spread_and_the_target_gain():
    # 30 cases over five rounds. Each case covers 4, 5 or 6 of its 10 golden findings in a round.
    cases = {f'case-{index:02}': [counts(6, 6, 4 + (index + number) % 3, unmatched_fp=index % 2) for number in range(5)]
             for index in range(30)}
    oracle = summary({name: [counts(10, 10, 10 - (index == 0 and number == 1)) for number in range(5)]
                      for index, name in enumerate(cases)}, side=None)
    loaded = settings.load(canary_cases=3)
    result = noise.derive(summary(cases), oracle, loaded)
    derived, blocks = result['derived'], result['reviewer']['blocks']
    # Rotating which case covers what keeps every round's pooled recall equal, so the spread is zero
    # and one round would do; the tiny sigma then sets every threshold.
    assert blocks['all']['grounded_f1']['sd'] == pytest.approx(0, abs=1e-12)
    assert derived['rounds'] == 1 and derived['climb_set_size'] == 25
    assert derived['accept_threshold']['2'] == pytest.approx(loaded['accept_z'] * derived['sigma'])
    assert result['judge']['cases_scored_differently'] == ['case-00']
    assert result['canary']['cases'] == ['case-00', 'case-01', 'case-02']
    assert result['canary']['reference']['case-00']['golden_covered_count'] == pytest.approx(9.8)
    # Case 00 moved by 1 in one round: 0.8 from its mean, times accept_z.
    assert result['canary']['tolerance'] == pytest.approx(2 * 0.8)


def test_a_spread_too_wide_for_the_target_says_so():
    noisy = {f'case-{index}': [counts(6, 6, [2, 8, 3, 9, 5][number]) for number in range(5)] for index in range(30)}
    oracle = summary({name: [counts(10, 10, 10)] * 5 for name in noisy}, side=None)
    derived = noise.derive(summary(noisy), oracle, settings.load())['derived']
    assert derived['climb_set_size'] is None and derived['rounds'] == 5
    assert derived['smallest_detectable_gain'] == pytest.approx(derived['accept_threshold']['5'])
    assert derived['smallest_detectable_gain'] > settings.load()['target_gain']
