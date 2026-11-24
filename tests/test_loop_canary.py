"""loop.canary over recorded oracle rounds, from the stand-in harbor run of test_loop_rounds."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.reviewbench import convert
from loop import canary, noise, rounds, sets, settings
import test_loop_noise
import test_loop_rounds
from test_loop_rounds import counts, write_job

workspace = test_loop_rounds.workspace
JUDGE = f'{convert.PROVIDER}/{convert.MODEL}'
STEADY = {'case-a': counts(2, 1, 1, 0, 2, 1), 'case-b': counts(2, 2, 1, 0, 2, 1)}
MOVED = {'case-a': counts(5, 4, 3, 0, 5, 3), 'case-b': STEADY['case-b']}


def test_the_canary_cases_are_the_climb_sets_first_and_the_ones_noise_references():
    loaded = settings.load()
    climb, first = sets.read(sets.CLIMB), sets.read(sets.CANARY)
    assert first == climb[:loaded['canary_cases']]
    # Rounds list their cases by name; the noise measurement still references these.
    steady = {case: [test_loop_noise.counts(5, 4, 4)] * 5 for case in sorted(climb)}
    derived = noise.derive(test_loop_noise.summary(steady), test_loop_noise.summary(steady, side=None), loaded)
    assert derived['canary']['cases'] == first


@pytest.fixture
def oracle(workspace, monkeypatch):
    """noise.json's canary block, and a harbor run that scores each round's oracle trials as told."""
    monkeypatch.setattr(canary, 'CASES', workspace / 'cases.txt')
    reference = {'case-a': dict(matched_count=1.8, matched_tp_count=1.2, unmatched_tp_count=0.0,
                                golden_covered_count=1.0),
                 'case-b': dict(matched_count=2.0, matched_tp_count=1.0, unmatched_tp_count=0.4,
                                golden_covered_count=1.0)}
    (workspace / 'noise.json').write_text(json.dumps(dict(derived={'sigma': 0.01}, canary=dict(
        cases=['case-a', 'case-b'], counts=list(noise.CANARY_COUNTS), judge=JUDGE, reference=reference,
        tolerance=1.6))))
    plan, calls = [], []
    def harbor(command, check):
        calls.append(command)
        write_job(Path('jobs') / command[command.index('--job-name') + 1], Path(command[command.index('-p') + 1]),
                  plan.pop(0), record=False)
    monkeypatch.setattr(rounds, 'subprocess', SimpleNamespace(run=harbor))
    return SimpleNamespace(noise=workspace / 'noise.json', plan=plan, calls=calls)


def check(capsys, *arguments) -> tuple[int, dict, str]:
    code = canary.main(list(arguments))
    out, err = capsys.readouterr()
    return code, json.loads(out), err


def test_counts_within_the_tolerance_are_stable(oracle, capsys):
    oracle.plan.append(STEADY)
    code, result, _ = check(capsys, '--name', 'canary-1', '--noise', str(oracle.noise))
    assert code == 0 and result['verdict'] == 'stable' and result['moved'] == result['unscored'] == []
    assert (result['job'], result['judge'], result['tolerance']) == ('canary-1-round-1-attempt-1', JUDGE, 1.6)
    assert result['cases']['case-a']['matched_count'] == pytest.approx(
        dict(reference=1.8, observed=1, delta=-0.8))
    assert '-c' in oracle.calls[0] and str(rounds.ORACLE_CONFIG) in oracle.calls[0]


def test_a_count_beyond_the_tolerance_or_an_unscored_case_is_drift(oracle, capsys):
    oracle.plan.append(MOVED)
    code, result, err = check(capsys, '--name', 'canary-1', '--noise', str(oracle.noise))
    assert code == 1 and result['verdict'] == 'drifted'
    assert result['moved'] == ['case-a'] and result['unscored'] == []
    assert result['cases']['case-a']['matched_count']['delta'] == pytest.approx(2.2)
    assert result['cases']['case-a']['golden_covered_count']['delta'] == pytest.approx(2)
    assert 'case-a moved: matched_count +2.2, matched_tp_count +1.8, golden_covered_count +2' in err
    # A round already scored, whose case-b trial left no score.
    catalog = Path('artifacts/loop/canary-1/catalog')
    write_job(Path('jobs/lost'), catalog, {'case-a': STEADY['case-a'], 'case-b': None}, record=False)
    summary = Path('lost.json')
    summary.write_text(json.dumps(dict(request={'build': 'oracle'}, **rounds.summarise(catalog, ['lost'], oracle=True))))
    code, result, err = check(capsys, '--summary', str(summary), '--noise', str(oracle.noise))
    assert code == 1 and result['moved'] == result['unscored'] == ['case-b'] and result['job'] == 'lost'
    assert result['cases']['case-b']['matched_count'] == dict(reference=2.0, observed=None, delta=None)
    assert 'case-b moved: unscored' in err
    # It never becomes the reference.
    before = oracle.noise.read_text()
    code, result, _ = check(capsys, '--summary', str(summary), '--noise', str(oracle.noise), '--record')
    assert code == 2 and "left ['case-b'] unscored" in result['error'] and oracle.noise.read_text() == before


def test_without_a_reference_nothing_runs_and_nothing_compares(oracle, capsys):
    oracle.noise.unlink()
    code, result, _ = check(capsys, '--name', 'canary-1', '--noise', str(oracle.noise))
    assert code == 2 and result['verdict'] == 'no-reference' and 'derive it with python -m loop.noise' in result['error']
    assert not oracle.calls and not Path('artifacts/loop/canary-1').exists()
    for block in (None, dict(cases=['case-a'], counts=['matched_count'], tolerance=1.0)):
        oracle.noise.write_text(json.dumps(dict(derived={}, canary=block)))
        code, result, _ = check(capsys, '--name', 'canary-1', '--noise', str(oracle.noise))
        assert code == 2 and result['verdict'] == 'no-reference'
    assert "No reference counts for ['case-a']" in result['error'] and not oracle.calls


def test_a_round_that_isnt_the_oracles_or_this_judges_is_refused(oracle, capsys):
    oracle.plan.append(STEADY)
    assert check(capsys, '--name', 'canary-1', '--noise', str(oracle.noise))[0] == 0
    summary = Path('artifacts/loop/canary-1/summary.json')
    state = json.loads(summary.read_text())
    for change, message in (({'request': {'build': 'pinned'}}, "--build oracle, not 'pinned'"),
                            ({'catalog_identity': {'judge': 'other/judge'}}, f'judge {JUDGE}, not other/judge'),
                            ({'cases': {'case-a': state['cases']['case-a']}}, "include the canary cases ['case-b']"),
                            ({'rounds': state['rounds'] * 2}, 'one finished round, not 2')):
        Path('changed.json').write_text(json.dumps(state | change))
        code, result, _ = check(capsys, '--summary', 'changed.json', '--noise', str(oracle.noise))
        assert code == 2 and result['verdict'] == 'invalid' and message in result['error']


def test_record_replaces_only_the_reference_from_a_fully_scored_round(oracle, capsys):
    oracle.plan.append(MOVED)
    assert check(capsys, '--name', 'canary-1', '--noise', str(oracle.noise))[0] == 1
    summary = 'artifacts/loop/canary-1/summary.json'
    # A person decided the judge changed, even its name.
    before = json.loads(oracle.noise.read_text())
    before['canary']['judge'] = 'old/judge'
    oracle.noise.write_text(json.dumps(before))
    code, result, _ = check(capsys, '--summary', summary, '--noise', str(oracle.noise), '--record')
    after = json.loads(oracle.noise.read_text())
    identity = json.loads(Path(summary).read_text())['catalog_identity']
    assert code == 0 and result == dict(recorded=str(oracle.noise), canary=after['canary'], replaced=dict(
        reference=before['canary']['reference'], judge='old/judge',
        catalog=dict(reviewbench_revision=None, split=None), source=None))
    assert after['derived'] == before['derived']
    assert after['canary'] == before['canary'] | dict(
        judge=JUDGE, source=dict(summary=summary, job='canary-1-round-1-attempt-1'),
        catalog={key: identity[key] for key in canary.IDENTITY},
        reference={case: {key: MOVED[case][key] for key in noise.CANARY_COUNTS} for case in MOVED})
    code, result, _ = check(capsys, '--summary', summary, '--noise', str(oracle.noise))
    assert code == 0 and result['verdict'] == 'stable'


def test_a_round_from_another_revision_or_split_than_the_reference_is_refused(oracle, capsys):
    oracle.plan.append(STEADY)
    assert check(capsys, '--name', 'canary-1', '--noise', str(oracle.noise))[0] == 0
    identity = json.loads(Path('artifacts/loop/canary-1/summary.json').read_text())['catalog_identity']
    data = json.loads(oracle.noise.read_text())
    for key in canary.IDENTITY:
        # A reference derived by loop.noise shares the measurement's catalog.
        data['catalog'] = {name: identity[name] for name in canary.IDENTITY} | {key: 'other'}
        oracle.noise.write_text(json.dumps(data))
        code, result, _ = check(capsys, '--summary', 'artifacts/loop/canary-1/summary.json',
                                '--noise', str(oracle.noise))
        assert code == 2 and result['verdict'] == 'invalid' and f'recorded on {key} other' in result['error']


def test_a_name_already_scored_is_refused_rather_than_compared_again(oracle, capsys):
    oracle.plan.append(STEADY)
    assert check(capsys, '--name', 'canary-1', '--noise', str(oracle.noise))[0] == 0
    oracle.plan.append(MOVED)
    code, result, _ = check(capsys, '--name', 'canary-1', '--noise', str(oracle.noise))
    assert code == 2 and 'scored already' in result['error'] and len(oracle.calls) == 1
    oracle.plan.clear()


def test_a_malformed_reference_is_no_verdict_not_drift(oracle, capsys):
    oracle.plan.append(STEADY)
    data = json.loads(oracle.noise.read_text())
    del data['canary']['counts']
    oracle.noise.write_text(json.dumps(data))
    code, result, _ = check(capsys, '--name', 'canary-1', '--noise', str(oracle.noise))
    assert code == 2 and result['verdict'] == 'invalid' and result['error'].startswith('KeyError')
