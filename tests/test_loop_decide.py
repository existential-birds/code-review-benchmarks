"""loop.decide on made-up round summaries in the shape loop.rounds writes."""
import json

import pytest

from loop import decide, settings
from test_loop_rounds import counts

LIMITS = settings.load(sigma=0.02, rounds=3, precision_margin=0.03, wrong_findings_margin=0.2,
                       incomplete_margin=0.5)
# Every case of the baseline: 1 of 2 matched findings true, 1 of 2 reference findings covered, and one
# unmatched finding judged valid. A winner covers both reference findings with the same findings.
BASE, WIN = counts(3, 2, 1, 1, 2, 1), counts(3, 2, 1, 1, 2, 2)
# The baseline's hard cases: nothing covered, and its unmatched finding judged wrong.
HARD = counts(3, 2, 0, 0, 2, 0)


def summary(per_case: list, rounds: int = 3, judge: str = 'j', cost: float | None = 2.5, seconds: float = 60,
            missing: int = 0) -> dict:
    """A summary over case-0, case-1, ...: each item is a case's counts in every round, or a list per round.
    A null is a trial that left no score (failed, or missing from the job when ``missing`` says so);
    each round's cost and time are per scored trial."""
    cases = {f'case-{number}': item if isinstance(item, list) else [item] * rounds
             for number, item in enumerate(per_case)}
    entries = []
    for number in range(rounds):
        scored = sum(counts[number] is not None for counts in cases.values())
        entries.append(dict(round=number + 1, expected=len(cases), failed=len(cases) - scored - missing,
                            missing=missing, incomplete=0,
                            evaluated=scored,
                            cost_usd=None if cost is None else cost * scored, cost_trials=scored,
                            elapsed_seconds=seconds * scored))
    return dict(catalog_identity=dict(reviewbench_revision='r', split='dev', judge=judge, daydream={'version': 'v'}),
                side='s' * 40, rounds=entries, cases=cases)


def failed_guards(result: dict) -> list[str]:
    return [name for name, guard in result['guards'].items() if not guard['passed']]


def test_a_real_gain_is_accepted_and_reports_recall_and_precision_apart():
    result = decide.decide(summary([BASE] * 10), summary([WIN] * 10), 'climb', LIMITS)
    assert result['verdict'] == 'accept' and failed_guards(result) == []
    assert result['needed_gain'] == pytest.approx(LIMITS.accept_threshold(3))
    # Recall rose from 0.5 to 1 with precision unchanged, so grounded F1 rose from 0.5 to 2/3.
    assert result['gain']['grounded_recall'] == pytest.approx(.5)
    assert result['gain']['grounded_precision'] == result['gain']['augmented_precision'] == 0
    assert result['gain']['grounded_f1'] == pytest.approx(1 / 6)
    assert result['matched'] == dict(cases=10, of=10, fraction=1, min_fraction=.8)
    assert len(result['candidate']['rounds']) == 3 and result['candidate']['mean']['cost_per_case'] == 2.5
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('candidate, failed', [
    # Two cases match three findings with one true: pooled grounded precision 10/22 against 1/2.
    (summary([WIN] * 8 + [counts(6, 3, 1, 3, 2, 2)] * 2), ['grounded_precision']),
    # The unmatched valid finding dropped: augmented precision 1/2 against 2/3.
    (summary([counts(2, 2, 1, 0, 2, 2)] * 10), ['augmented_precision']),
    # Half the cases add an unmatched finding judged wrong, with enough valid ones to hold augmented precision.
    (summary([WIN] * 5 + [counts(6, 2, 1, 3, 2, 2)] * 5), ['wrong_findings_per_case']),
    # Seven findings per case, all valid, against three.
    (summary([counts(7, 2, 1, 5, 2, 2)] * 10), ['findings_per_case']),
    # One case fails in every round: one failed review per round against none.
    (summary([WIN] * 9 + [None]), ['unfinished']),
    (summary([WIN] * 9 + [None], missing=1), ['unfinished']),
    (summary([WIN] * 10, cost=3.2), ['cost_per_case']),
    (summary([WIN] * 10, seconds=80), ['seconds_per_case']),
])
def test_each_guard_rejects_a_gain_on_its_own(candidate, failed):
    result = decide.decide(summary([BASE] * 10), candidate, 'climb', LIMITS)
    assert result['gain']['grounded_f1'] > result['needed_gain']
    assert result['verdict'] == 'reject' and failed_guards(result) == failed and len(result['reasons']) == 1
    assert result['reasons'][0].startswith(failed[0])


def test_a_flood_raises_recall_and_f1_but_is_rejected():
    flood = summary([counts(13, 2, 1, 1, 2, 2)] * 10)
    result = decide.decide(summary([BASE] * 10), flood, 'climb', LIMITS)
    # Grounded precision ignores the ten unmatched findings judged wrong in every case.
    assert result['gain']['grounded_recall'] == pytest.approx(.5) and result['gain']['grounded_precision'] == 0
    assert result['gain']['grounded_f1'] > result['needed_gain']
    assert result['verdict'] == 'reject'
    assert failed_guards(result) == ['augmented_precision', 'wrong_findings_per_case', 'findings_per_case']
    assert result['guards']['wrong_findings_per_case']['candidate'] == 10


def test_a_candidate_that_fails_on_the_baselines_worst_cases_is_rejected_or_invalid():
    baseline = summary([BASE] * 8 + [HARD] * 2)
    dodger = summary([BASE] * 8 + [None] * 2)
    # Pooled over the cases each scored, the dodger would look better than the baseline it copies.
    every, easy = sorted(baseline['cases']), sorted(baseline['cases'])[:8]
    assert decide.means(decide.side_rounds(dodger, easy))['grounded_f1'] > decide.means(
        decide.side_rounds(baseline, every))['grounded_f1']
    result = decide.decide(baseline, dodger, 'climb', LIMITS)
    assert decide.matched_cases(baseline, dodger) == easy and result['matched']['fraction'] == .8
    assert result['verdict'] == 'reject' and result['gain']['grounded_f1'] == 0
    assert failed_guards(result) == ['unfinished'] and len(result['reasons']) == 2
    # Failing on a third case leaves too few to compare, after its first round already.
    worse = [BASE] * 7 + [None] * 3
    for rounds in (1, 3):
        result = decide.decide(summary([BASE] * 7 + [HARD] * 3), summary(worse, rounds=rounds), 'climb', LIMITS)
        assert result['verdict'] == 'invalid' and 'Only 7 of 10 cases' in result['reasons'][0]


def test_a_gain_inside_the_threshold_is_rejected_and_the_check_stage_needs_a_gain_above_zero():
    baseline, small = summary([BASE] * 10), summary([WIN] + [BASE] * 9)
    climb = decide.decide(baseline, small, 'climb', LIMITS)
    assert 0 < climb['gain']['grounded_f1'] < climb['needed_gain']
    assert climb['verdict'] == 'reject' and climb['reasons'][0].startswith('The grounded F1 gain')
    assert failed_guards(climb) == []
    check = decide.decide(baseline, small, 'check', LIMITS)
    assert check['needed_gain'] == 0 and check['verdict'] == 'accept'
    same = decide.decide(baseline, summary([BASE] * 10), 'check', LIMITS)
    assert same['gain']['grounded_f1'] == 0 and same['verdict'] == 'reject'


def test_an_early_stop_rejects_once_the_threshold_is_out_of_reach_and_never_accepts():
    baseline = summary([BASE] * 10)
    # The same build: after one round the two remaining could still add 2/3 * 2 * 0.02 * sqrt(2) = 0.038,
    # above the threshold of 0.033; after two rounds the last could add only 0.019.
    first = decide.decide(baseline, summary([BASE] * 10, rounds=1), 'climb', LIMITS)
    assert first['verdict'] == 'continue' and first['early_stop']['best_final_gain'] == pytest.approx(
        2 / 3 * 2 * .02 * 2 ** .5)
    second = decide.decide(baseline, summary([BASE] * 10, rounds=2), 'climb', LIMITS)
    assert second['verdict'] == 'reject' and second['reasons'][0].startswith('After 2 of 3 rounds')
    assert 'guards' not in second
    # A clearly worse build stops after one round; a winner, even with every guard failing, continues.
    worse = decide.decide(baseline, summary([counts(3, 2, 1, 1, 2, 0)] * 10, rounds=1), 'climb', LIMITS)
    assert worse['verdict'] == 'reject'
    for rounds in (1, 2):
        winner = decide.decide(baseline, summary([counts(13, 2, 1, 1, 2, 2)] * 10, rounds=rounds), 'climb', LIMITS)
        assert winner['verdict'] == 'continue'
    off = settings.load(sigma=.02, rounds=3, precision_margin=.03, wrong_findings_margin=.2, incomplete_margin=.5,
                        futility_stop=False)
    assert decide.decide(baseline, summary([BASE] * 10, rounds=2), 'climb', off)['verdict'] == 'continue'


def test_summaries_that_cannot_be_compared_are_refused():
    baseline = summary([BASE] * 10)
    other_build = summary([WIN] * 10)
    other_build['catalog_identity']['daydream'] = {'version': 'candidate'}
    assert decide.decide(baseline, other_build, 'climb', LIMITS)['verdict'] == 'accept'
    for key in ('reviewbench_revision', 'split', 'judge'):
        other = summary([WIN] * 10)
        other['catalog_identity'][key] = 'other'
        with pytest.raises(ValueError, match=f'Refusing to compare summaries of different {key}'):
            decide.decide(baseline, other, 'climb', LIMITS)
    with pytest.raises(ValueError, match='different review cases'):
        decide.decide(baseline, summary([WIN] * 11), 'climb', LIMITS)
    with pytest.raises(ValueError, match='baseline needs 3 rounds and the candidate at most 3, not 3 and 4'):
        decide.decide(baseline, summary([WIN] * 10, rounds=4), 'climb', LIMITS)
    with pytest.raises(ValueError, match='not 2 and 2'):
        decide.decide(summary([BASE] * 10, rounds=2), summary([WIN] * 10, rounds=2), 'climb', LIMITS)
    # A figure no round can define makes the comparison invalid rather than guessed.
    unpriced = decide.decide(baseline, summary([WIN] * 10, cost=None), 'climb', LIMITS)
    assert unpriced['verdict'] == 'invalid' and unpriced['reasons'] == [
        'Undefined in some round on the matched cases: cost_per_case']


def test_the_command_prints_the_verdict_or_why_it_could_not_compare(tmp_path, monkeypatch, capsys):
    paths = {}
    for name, per_case in (('baseline', [BASE] * 10), ('candidate', [WIN] * 10)):
        paths[name] = tmp_path / f'{name}.json'
        paths[name].write_text(json.dumps(summary(per_case)))
    arguments = ['--baseline', str(paths['baseline']), '--candidate', str(paths['candidate']), '--stage', 'climb']
    # Without the noise measurement's derived values, no verdict is given.
    unmeasured = tmp_path / 'settings.toml'
    unmeasured.write_text(settings.FILE.read_text().split('[derived]')[0] + '[derived]\n')
    real_load = settings.load
    monkeypatch.setattr(settings, 'load', lambda: real_load(unmeasured))
    assert decide.main(arguments) == 2 and 'rounds is unset' in json.loads(capsys.readouterr().out)['error']
    monkeypatch.setattr(settings, 'load', lambda: LIMITS)
    assert decide.main(arguments) == 0
    assert json.loads(capsys.readouterr().out)['verdict'] == 'accept'


def test_self_check_counts_how_often_a_build_beats_itself():
    # Rounds that differ by one covered reference finding, inside the noise of sigma = 0.02.
    steady = summary([[WIN, BASE, WIN, BASE, WIN]] + [BASE] * 9, rounds=5)
    result = decide.self_check(steady, LIMITS)
    assert result['group_rounds'] == 2 and result['decisions'] == 10 * 3
    assert result['beats_itself'] == result['beats_itself_on_gain'] == 0 and result['verdicts'] == {'reject': 30}
    # Rounds that drift upwards well beyond it: the later rounds beat the earlier ones.
    drifting = summary([[BASE] * (5 - number) + [WIN] * number for number in range(5)] * 2, rounds=5)
    drift = decide.self_check(drifting, LIMITS)
    assert 0 < drift['beats_itself'] < 30 and drift['verdicts']['accept'] == drift['beats_itself']
    assert drift['beats_itself_on_gain'] >= drift['beats_itself']
    with pytest.raises(ValueError, match='at least two rounds'):
        decide.self_check(summary([BASE] * 10, rounds=1), LIMITS)


def test_a_candidate_that_matched_nothing_is_rejected_not_invalid():
    # Every review came back empty: recall 0, and no matched finding to give a precision.
    empty = counts(0, 0, 0, 0, 2, 0)
    for stage, rounds in (('climb', 3), ('check', 3), ('climb', 1)):
        result = decide.decide(summary([BASE] * 10), summary([empty] * 10, rounds=rounds), stage, LIMITS)
        assert result['verdict'] == 'reject' and 'matched no findings' in result['reasons'][0]
        assert 'grounded_precision' in result['reasons'][0]


def test_two_summaries_without_cases_are_invalid_not_a_crash():
    empty = summary([])
    result = decide.decide(empty, summary([]), 'climb', LIMITS)
    assert result['verdict'] == 'invalid' and result['matched']['fraction'] == 0
