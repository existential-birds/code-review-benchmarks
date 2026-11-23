import fcntl
import hashlib
import json

import pytest

from loop import ledger

RUN_START = {'settings': {'concurrency': 2}, 'suite_commit': 'a' * 40, 'judge': 'pi', 'baseline_commit': 'b' * 40,
             'baseline_summary_sha256': '0' * 64, 'canary': {'drift': False}}
PATCH = 'diff --git a/prompt.md b/prompt.md\n--- a/prompt.md\n+++ b/prompt.md\n@@ -1 +1 @@\n-old\n+new\n'


def iteration(n, verdict='rejected', reasons=('gain below threshold',), **proposal):
    return {'iteration': n, 'verdict': {'verdict': verdict, 'reasons': list(reasons)},
            'proposal': {'hypothesis': f'Hypothesis {n}.', 'category': 'handoff_loss', 'evidence': ['run:1'],
                         'prediction': 'recall up', 'rejection_criterion': 'gain below threshold',
                         'files': ['prompts/review.md'], **proposal},
            'candidate': {'commit': 'c' * 40, 'patch_id': f'{n}' * 40, 'diff_lines': 2, 'wheel_sha256': '1' * 64},
            'gate': {'guard': 'passed', 'make_check': 'passed', 'seconds': 80},
            'scores': {'climb': {'gain': 0.004, 'threshold': 0.02}}, 'cost': {'trials': 50}}


@pytest.fixture
def run(tmp_path):
    run = tmp_path / 'run-a'
    ledger.append(run, 'run-start', RUN_START)
    ledger.append(run, 'iteration', iteration(1), patch=PATCH)
    ledger.append(run, 'iteration', iteration(2, 'accepted-on-dev', ['gain 0.03 over threshold 0.02']), patch=PATCH)
    ledger.append(run, 'confirmation', {'iteration': 2, 'verdict': 'not-confirmed'})
    ledger.append(run, 'stop', {'reason': 'plateau'})
    return run


def test_entries_chain_each_line_to_the_previous_and_save_the_patch(run):
    lines = (run / 'ledger.jsonl').read_bytes().splitlines()
    entries = ledger.read(run)
    assert [entry['kind'] for entry in entries] == ['run-start', 'iteration', 'iteration', 'confirmation', 'stop']
    assert entries[0]['prev_sha256'] is None
    assert [entry['prev_sha256'] for entry in entries[1:]] == [hashlib.sha256(line).hexdigest() for line in lines[:-1]]
    assert all(entry['schema_version'] == 1 and entry['run'] == 'run-a' and entry['time'] for entry in entries)
    assert (run / 'patches/1.patch').read_text() == (run / 'patches/2.patch').read_text() == PATCH


def test_an_edited_or_removed_line_breaks_the_chain(run):
    path = run / 'ledger.jsonl'
    lines = path.read_text().splitlines(keepends=True)
    edited = json.loads(lines[1])
    edited['verdict']['verdict'] = 'accepted-on-dev'
    for written, broken in (([lines[0], json.dumps(edited) + '\n', *lines[2:]], 3),
                            ([lines[0], *lines[2:]], 2),
                            (lines[1:], 1)):
        path.write_text(''.join(written))
        with pytest.raises(ValueError, match=rf'line {broken}: .*edited, removed or inserted'):
            ledger.read(run)


def test_a_torn_last_line_is_reported_and_nothing_is_appended_after_it(run, monkeypatch, capsys):
    path = run / 'ledger.jsonl'
    path.write_bytes(path.read_bytes() + b'{"schema_version": 1, "run": "ru')
    before = path.read_bytes()
    with pytest.raises(ValueError, match='line 6 is torn'):
        ledger.read(run)
    with pytest.raises(ValueError, match='line 6 is torn'):
        ledger.append(run, 'stop', {'reason': 'budget'})
    assert path.read_bytes() == before
    monkeypatch.setattr(ledger, 'RUNS', run.parent)
    assert ledger.main(['verify', 'run-a']) == 1
    assert 'line 6 is torn' in capsys.readouterr().err


def test_no_held_out_score_is_stored(run):
    before = (run / 'ledger.jsonl').read_bytes()
    ledger.append(run, 'iteration', iteration(3, 'accepted-on-dev'), patch=PATCH)
    for fields, message in (
            ({'iteration': 3, 'verdict': 'confirmed', 'grounded_f1': 0.41}, r'confirmation entry holds .* only'),
            ({'iteration': 3, 'verdict': 'confirmed', 'scores': {'gain': 0.03}}, r'confirmation entry holds .* only'),
            ({'iteration': 3, 'verdict': 0.03}, 'one word'),
            ({'iteration': 3, 'verdict': {'verdict': 'confirmed', 'gain': 0.03}}, 'one word'),
            ({'iteration': 1, 'verdict': 'confirmed'}, 'iteration 1 is not'),
            ({'iteration': 2, 'verdict': 'confirmed'}, 'already confirmed')):
        with pytest.raises(ValueError, match=message):
            ledger.append(run, 'confirmation', fields)
    held_out = iteration(4) | {'scores': {'climb': {}, 'holdout': {'gain': 0.03}}}
    with pytest.raises(ValueError, match='climb set and, when run, the check set only'):
        ledger.append(run, 'iteration', held_out, patch=PATCH)
    in_verdict = iteration(4) | {'verdict': {'verdict': 'rejected', 'reasons': [], 'holdout_f1': 0.41}}
    with pytest.raises(ValueError, match='verdict is exactly'):
        ledger.append(run, 'iteration', in_verdict, patch=PATCH)
    with pytest.raises(ValueError, match='stop reason is a string'):
        ledger.append(run, 'stop', {'reason': {'held_out_f1': 0.41}})
    assert (run / 'ledger.jsonl').read_bytes().startswith(before)
    assert len(ledger.read(run)) == 6


def test_entries_that_do_not_fit_the_schema_are_refused(tmp_path, run):
    for target, kind, fields, patch, message in (
            (tmp_path / 'run-b', 'stop', {'reason': 'budget'}, None, 'starts with one run-start'),
            (run, 'run-start', RUN_START, None, 'starts with one run-start'),
            (run, 'iteration', iteration(1), PATCH, 'Iteration 1 is already in the ledger'),
            (run, 'iteration', iteration(5), None, 'saves its patch'),
            (run, 'iteration', iteration(5, 'kept'), PATCH, 'iteration verdict'),
            (run, 'iteration', iteration(5, files='prompts/review.md'), PATCH, 'lists of strings'),
            (run, 'iteration', iteration(5, files=[1]), PATCH, 'lists of strings'),
            (run, 'iteration', iteration(5, evidence='run:1'), PATCH, 'lists of strings'),
            (run, 'iteration', iteration(5, prediction=None), PATCH, 'lists of strings'),
            (run, 'iteration', iteration(5) | {'cost': {'trials': float('nan')}}, PATCH, 'not JSON compliant'),
            (run, 'stop', {'reason': 'budget', 'note': 'x'}, None, r'stop entry holds \[.reason.\] only'),
            (run, 'pause', {}, None, 'Unknown ledger entry kind')):
        with pytest.raises(ValueError, match=message):
            ledger.append(target, kind, fields, patch=patch)
    no_proposal = {'iteration': 5, 'verdict': {'verdict': 'invalid', 'reasons': ['no proposal']}}
    assert ledger.append(run, 'iteration', no_proposal)['prev_sha256']
    assert not (run / 'patches/5.patch').exists()
    path = run / 'ledger.jsonl'
    lines = path.read_text().splitlines(keepends=True)
    without_time, with_nan, list_kind = json.loads(lines[-1]), json.loads(lines[-1]), json.loads(lines[-1])
    del without_time['time']
    with_nan['cost'] = {'trials': float('nan')}
    list_kind['kind'] = []
    for entry, message in ((without_time, 'line 6: it has no time'), (with_nan, 'line 6: NaN is not strict JSON'),
                           (list_kind, r'line 6: Unknown ledger entry kind: \[\]')):
        path.write_text(''.join(lines[:-1]) + json.dumps(entry) + '\n')
        with pytest.raises(ValueError, match=message):
            ledger.read(run)


def test_ideas_lists_every_proposal_across_runs(tmp_path, run, monkeypatch, capsys):
    other = tmp_path / 'run-b'
    ledger.append(other, 'run-start', RUN_START)
    ledger.append(other, 'iteration', {'iteration': 1, 'verdict': {'verdict': 'invalid', 'reasons': ['no proposal']}})
    ledger.append(other, 'iteration', iteration(2, 'invalid', [], category='verification_gap',
                                                files=['a.py', 'b.py']), patch=PATCH)
    ledger.append(other, 'iteration', iteration(3, 'rejected', ['gain\n  0.001', 'precision fell'],
                                                hypothesis='Split\nreviews keep\tGo context.'), patch=PATCH)
    expected = [
        'run-a #1 rejected [handoff_loss] Hypothesis 1. | files: prompts/review.md | reason: gain below threshold',
        'run-a #2 accepted-on-dev, then not-confirmed on held-out [handoff_loss] Hypothesis 2. '
        '| files: prompts/review.md | reason: gain 0.03 over threshold 0.02',
        'run-b #2 invalid [verification_gap] Hypothesis 2. | files: a.py, b.py | reason: none given',
        'run-b #3 rejected [handoff_loss] Split reviews keep Go context. | files: prompts/review.md '
        '| reason: gain 0.001; precision fell',
    ]
    assert ledger.ideas([run, other]) == expected
    monkeypatch.setattr(ledger, 'RUNS', tmp_path)
    assert ledger.main(['ideas', 'run-a', 'run-c', 'run-b']) == 0
    assert capsys.readouterr().out.splitlines() == expected
    head = hashlib.sha256((run / 'ledger.jsonl').read_bytes().splitlines()[-1]).hexdigest()
    assert ledger.main(['verify', 'run-a']) == 0
    assert capsys.readouterr().out == f'run-a: 5 entries, chain intact, last line sha256 {head}\n'
    assert ledger.main(['show', 'run-a']) == 0
    assert f'# run-a line 5 sha256 {head}\n' in capsys.readouterr().out
    for command in ('verify', 'show'):
        assert ledger.main([command, 'run-a', 'run-c']) == 1
        assert 'No ledger entries in ' in capsys.readouterr().err
    with pytest.raises(SystemExit):
        ledger.main(['ideas', '../confirmations'])


def test_the_ledger_stays_locked_from_verifying_to_appending(run, monkeypatch):
    verified, held = ledger._verified, []

    def verified_while_locked(target):
        with open(target / 'ledger.jsonl', 'rb') as other_writer:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other_writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held.append(True)
        return verified(target)

    monkeypatch.setattr(ledger, '_verified', verified_while_locked)
    ledger.append(run, 'stop', {'reason': 'budget'})
    assert held == [True]
