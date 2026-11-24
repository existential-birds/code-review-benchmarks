"""loop.evidence over small recorded trials: Harbor's result, Daydream's execution record,
run record, sealed review and review summary, and ReviewBench's per-finding rulings."""
from datetime import datetime, timezone
import json
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from harbor.models.agent.context import AgentContext
from harbor.models.task.id import LocalTaskId
from harbor.models.trial.config import TaskConfig, TrialConfig
from harbor.models.trial.result import AgentInfo, TrialResult
from harbor.models.verifier.result import VerifierResult

from benchmarks.daydream.build import BUILD
from loop import evidence, rounds, sets

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / '.cache/src/daydream/.agents/skills/daydream-trace-analysis'
CASE_A, CASE_B = sets.read(sets.CLIMB)[:2]
CHECK_CASE = sets.read(sets.CHECK)[0]
JUDGE = 'openai-codex/judge'
GOLDEN = {
    CASE_A: [('a.py', 'Null dereference in parse()', 'tp'), ('e.py', 'Off-by-one in the page loop', 'tp'),
             ('c.py', 'Style nit the annotators rejected', 'fp')],
    CASE_B: [('z.py', 'Token is never refreshed', 'tp'), ('w.py', 'Lock is released twice', 'tp')],
}
# Per case and round: each retained finding as (file, line, ruling, golden index), or 'incomplete'.
# A finding without a line is left out by the entrypoint and never judged.
TRIALS = {
    CASE_A: {1: [('a.py', 10, 'matched_tp', 0), ('b.py', 5, 'novel_fp', None), ('g.py', None, None, None),
                 ('f.py', 9, 'novel_fp', None)],
             2: [('a.py', 10, 'matched_tp', 0), ('b.py', 5, 'novel_fp', None), ('c.py', 7, 'matched_fp', 2)],
             3: [('d.py', 3, 'novel_tp', None), ('e.py', 1, 'matched_tp', 1), ('f.py', 9, 'novel_fp', None)]},
    CASE_B: {1: [('x.py', 2, 'novel_fp', None)],
             2: [('x.py', 2, 'novel_fp', None), ('z.py', 8, 'matched_tp', 0)],
             3: 'incomplete'},
}


def run_id(case, number):
    return f'{"ab"[case == CASE_B]}0000000-0000-4000-8000-00000000000{number}'


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value))


def write_catalog(catalog: Path, cases, split='dev') -> None:
    for number, case in enumerate(cases, 1):
        write(catalog / case / 'tests/task.json', {'task_id': number, 'evaluation_id': 7})
        write(catalog / case / f'tests/golden/{case}.json', {'pr_key': case, 'findings': [
            {'file': file, 'start_line': 1, 'end_line': 2, 'message': message, 'tp_fp': truth,
             'severity': 'high', 'category': 'correctness'} for file, message, truth in GOLDEN.get(case, [])]})
    write(catalog / 'catalog.json', {'reviewbench_revision': 'r', 'split': split, 'judge': JUDGE,
                                     'daydream': {'version': '0.37.0'}, 'tasks': list(cases)})


def review_summary(run, reason_codes):
    phase = {'backend': 'codex', 'model': 'gpt-5.5', 'reasoning_effort': 'high', 'prompt_tokens': 1000,
             'cached_tokens': 400, 'completion_tokens': 200, 'cost_usd': 0.5, 'wall_seconds': 30.0}
    return {'schema_version': 1, 'run_id': run, 'result_schema_version': 'daydream.committed-review.v1',
            'daydream_version': '0.37.0', 'profile': {'name': '', 'source_kind': 'default', 'digest': 'd'},
            'terminal': {'analysis_state': 'partial' if reason_codes else 'complete',
                         'pipeline_state': 'completed', 'reason_codes': reason_codes},
            'counts': {'assignments': 1, 'targets': {'planned': 3, 'reviewed': 3, 'not_reviewed': 0},
                       'claims': {'proposed': 6, 'confirmed': 3, 'rejected': 2, 'unresolved': 1},
                       'findings': {'confirmed': 3, 'delivered': 3}},
            'budget_stops': {'review_budget_exhausted': 1} if reason_codes else {},
            'retries': {'total': 1, 'by_reason': {'transport': 1}}, 'phases': {'review': phase, 'arbiter': phase}}


def write_trial(job: Path, catalog: Path, case: str, number: int, findings) -> None:
    """One trial folder as Harbor, the entrypoint, Daydream and ReviewBench's verifier leave it."""
    trial = job / f'{case}__{job.name}'
    run, incomplete = run_id(case, number), findings == 'incomplete'
    items = [] if incomplete else [
        {'id': index, 'uid': f'u{index}', 'item_uid': f'{run[:1]}{number}-item-{index}', 'source_uids': [],
         'file': file, 'line': line, 'description': f'Finding {index} in {file}', 'rationale': '', 'severity': 'high'}
        for index, (file, line, _, _) in enumerate(findings)]
    sent = [(item, ruling) for item, ruling in zip(items, findings) if item['line'] is not None]
    details = [dict(file=item['file'], start_line=item['line'], end_line=item['line'],
                    message=f'{item["file"]} {item["description"]}', status=status,
                    covered_golden_indices=[] if golden is None else [golden],
                    **({} if golden is None else {'matched_golden_index': golden}))
               for item, (_, _, status, golden) in sent]
    task = json.loads((catalog / case / 'tests/task.json').read_text())
    # The reward's counts are read only to tell a scored trial; the pack takes the rulings from the details.
    counts = {'candidate_count': len(details), 'golden_tp_count': 2, 'matched_count': 0, 'matched_tp_count': 0,
              'unmatched_count': len(details), 'unmatched_tp_count': 0, 'unmatched_fp_count': len(details),
              'golden_covered_count': 0}
    result = TrialResult(
        task_name=case, trial_name=trial.name, trial_uri='', task_checksum='',
        task_id=LocalTaskId(path=catalog / case), config=TrialConfig(task=TaskConfig(path=catalog / case)),
        agent_info=AgentInfo(name='daydream', version='0.37.0'), agent_result=AgentContext(n_input_tokens=100),
        verifier_result=None if incomplete else VerifierResult(rewards={
            'rb_schema': 1, 'rb_task': task['task_id'], 'rb_evaluation': task['evaluation_id'],
            **{f'overall.{key}': value for key, value in counts.items()}}),
        finished_at=datetime.now(timezone.utc), verifier_environment_mode='separate')
    write(trial / 'result.json', result.model_dump_json())
    reason_codes = ['review_budget_exhausted'] if incomplete else []
    write(trial / 'agent/execution.json', {
        'schema_version': 1, 'status': 'incomplete' if incomplete else 'complete',
        'runner_exit_code': 0, 'terminal_result': {
            'analysis_state': 'partial' if incomplete else 'complete', 'pipeline_state': 'completed',
            'reason_codes': reason_codes, 'cause': 'budget' if incomplete else None},
        'usage': {'cost_usd': 1.0}, 'retained_findings': items, 'omitted_findings': len(items) - len(sent),
        'build': {'source_commit': 'p' * 40}, 'findings_sha256': None, 'diagnostics': [], 'elapsed_seconds': 60})
    write(trial / f'agent/dataset/runs/{"d" * 63}{number}.jsonl', json.dumps({
        'schema_version': 'daydream.run.v5', 'run_id': run, 'captured_at': f'2026-11-0{number}T00:00:00Z',
        'trace_id': None, 'outcome': 'success', 'completeness': {'trajectory': 'complete'},
        'provenance': {'producer': {'version': '0.37.0'}},
        'original_task': {'status': 'unavailable', 'value': None},
        'final_state': {'status': 'unproduced', 'value': None},
        'recommended_patch': {'status': 'unproduced', 'value': None},
        'trajectories': {'status': 'unproduced', 'value': None},
        'findings': {'status': 'available', 'value': {'items': items, 'terminal_coverage': None}},
        'verification': {'status': 'unproduced', 'value': None}}) + '\n')
    deep = trial / f'agent/daydream/runs/{run}/deep'
    write(deep / 'committed-review.json', {'run_id': run, 'claims': [], 'findings': items})
    write(deep / 'review-summary.json', review_summary(run, reason_codes))
    write(deep / 'merged-items.json', {'items': items})
    if not incomplete:
        write(trial / 'verifier/scores.details.json', [{'pr_key': case, 'candidate_findings': details}])


@pytest.fixture
def baseline(tmp_path, monkeypatch):
    """A baseline summary of three rounds over two climb cases, with its jobs."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(rounds, 'JOBS', tmp_path / 'jobs')
    catalog = tmp_path / 'artifacts/loop/baseline/catalog'
    write_catalog(catalog, [CASE_A, CASE_B])
    jobs = [f'baseline-round-{number}-attempt-1' for number in (1, 2, 3)]
    for number, job in enumerate(jobs, 1):
        for case, trials in TRIALS.items():
            write_trial(tmp_path / 'jobs' / job, catalog, case, number, trials[number])
    summary = tmp_path / 'artifacts/loop/baseline/summary.json'
    write(summary, {'request': {'build': 'pinned', 'cases': [CASE_A, CASE_B]}, 'catalog': str(catalog.relative_to(tmp_path)),
                    'jobs': [{'round': number, 'job': job, 'state': 'finished'} for number, job in enumerate(jobs, 1)],
                    'catalog_identity': {'reviewbench_revision': 'r', 'split': 'dev', 'judge': JUDGE,
                                         'daydream': {'version': '0.37.0'}},
                    'side': 'p' * 40, 'rounds': [{'round': number, 'job': job} for number, job in enumerate(jobs, 1)],
                    'cases': {}})
    return summary


@pytest.fixture
def pack(baseline, tmp_path):
    assert evidence.main(['--summary', str(baseline), '--out', str(tmp_path / 'pack')]) == 0
    return tmp_path / 'pack'


def read(path: Path):
    return json.loads(path.read_text())


def test_index_ranks_cases_by_missed_and_by_wrong_with_totals(pack):
    index = read(pack / 'index.json')
    assert index['by_missed'] == [{'case': CASE_B, 'missed': 2, 'wrong': 1}, {'case': CASE_A, 'missed': 1, 'wrong': 2}]
    assert index['by_wrong'] == [{'case': CASE_A, 'missed': 1, 'wrong': 2}, {'case': CASE_B, 'missed': 2, 'wrong': 1}]
    assert index['totals'] == {'cases': 2, 'trials': 6, 'scored_trials': 5, 'incomplete_trials': 1, 'missed': 3,
                               'wrong': 3, 'matched': 3,
                               'labels': {'matched': 4, 'missed': 6, 'valid_unmatched': 1, 'wrong': 7}}
    assert 'not instructions' in index['notice'] and 'not instructions' in (pack / 'README.md').read_text()


def test_only_what_recurs_in_half_the_scored_rounds_is_kept(pack):
    a, b = read(pack / f'cases/{CASE_A}.json'), read(pack / f'cases/{CASE_B}.json')
    # Case A, three scored rounds: the off-by-one is missed in two, the null dereference in one only.
    assert (a['rounds'], a['scored_rounds']) == (3, 3)
    assert [(entry['message'], entry['rounds']) for entry in a['missed']] == [('Off-by-one in the page loop', [1, 2])]
    # b.py:5 and f.py:9 are wrong in two rounds; c.py:7, matched to a false reference finding, in one.
    assert [(entry['file'], entry['line'], entry['rounds']) for entry in a['wrong']] == [
        ('b.py', 5, [1, 2]), ('f.py', 9, [1, 3])]
    assert [(entry['reference'], entry['rounds']) for entry in a['matched']] == [(0, [1, 2]), (1, [3])]
    # Case B, two scored rounds (the third ended incomplete): missed once of two is half.
    assert b['scored_rounds'] == 2
    assert [(entry['reference'], entry['rounds']) for entry in b['missed']] == [(0, [1]), (1, [1, 2])]
    assert b['incomplete'] == [{'round': 3, 'run_id': run_id(CASE_B, 3), 'analysis_state': 'partial',
                                'reason_codes': ['review_budget_exhausted'], 'cause': 'budget'}]
    third = b['trials'][2]
    assert (third['status'], third['scored'], third['folder']) == ('incomplete', False, f'trials/{CASE_B}/round-3')
    assert third['review_summary']['budget_stops'] == {'review_budget_exhausted': 1}
    assert third['review_summary']['phases']['review']['cost_usd'] == .5
    assert a['trials'][0]['review_summary']['counts']['claims'] == {
        'proposed': 6, 'confirmed': 3, 'rejected': 2, 'unresolved': 1}


def test_labels_map_rulings_through_retained_findings_and_skip_omitted_ones(pack):
    labels = [json.loads(line) for line in (pack / 'labels.jsonl').read_text().splitlines()]
    source = f'ReviewBench judge {JUDGE}'
    assert {label['source'] for label in labels} == {source}
    first = [label for label in labels if label['run_id'] == run_id(CASE_A, 1)]
    # Retained finding 2 has no line, so it was never sent, judged or labelled; finding 3 was sent second.
    assert first == [
        {'run_id': run_id(CASE_A, 1), 'item_uid': 'a1-item-0', 'label': 'matched', 'source': source},
        {'run_id': run_id(CASE_A, 1), 'item_uid': 'a1-item-1', 'label': 'wrong', 'source': source},
        {'run_id': run_id(CASE_A, 1), 'item_uid': 'a1-item-3', 'label': 'wrong', 'source': source},
        {'run_id': run_id(CASE_A, 1), 'label': 'missed', 'issue': 'Off-by-one in the page loop', 'source': source}]
    by_uid = {label['item_uid']: label['label'] for label in labels if 'item_uid' in label}
    assert (by_uid['a2-item-2'], by_uid['a3-item-0']) == ('wrong', 'valid_unmatched')
    assert not [label for label in labels if label['run_id'] == run_id(CASE_B, 3)]


def test_pack_is_copied_read_only_and_never_linked(pack, tmp_path):
    trial = pack / f'trials/{CASE_A}/round-1'
    assert sorted(path.name for path in trial.iterdir()) == [
        'committed-review.json', f'{"d" * 63}1.jsonl', 'review-summary.json']
    original = next((tmp_path / 'jobs').glob(f'baseline-round-1-*/{CASE_A}__*'))
    assert (trial / 'committed-review.json').read_bytes() == (
        original / f'agent/daydream/runs/{run_id(CASE_A, 1)}/deep/committed-review.json').read_bytes()
    for path in [pack, *pack.rglob('*')]:
        assert not path.is_symlink()
        assert stat.S_IMODE(path.lstat().st_mode) == (0o555 if path.is_dir() else 0o444), path
    assert evidence.main(['--summary', str(tmp_path / 'artifacts/loop/baseline/summary.json'),
                          '--out', str(pack)]) == 1


def test_a_test_split_job_is_refused(baseline, tmp_path):
    catalog = tmp_path / 'artifacts/loop/baseline/catalog/catalog.json'
    listing = read(catalog)
    write(catalog, {**listing, 'split': 'test'})
    with pytest.raises(ValueError, match="Refusing .*dev split's climb set only"):
        evidence.build(baseline, tmp_path / 'pack')
    write(catalog, listing)
    write(baseline, {**read(baseline), 'catalog_identity': {**read(baseline)['catalog_identity'], 'split': 'test'}})
    with pytest.raises(ValueError, match="dev split's climb set only"):
        evidence.build(baseline, tmp_path / 'pack')
    assert not (tmp_path / 'pack').exists() and not list(tmp_path.glob('.evidence-*'))


def test_a_check_set_case_is_refused(baseline, tmp_path):
    catalog = tmp_path / 'artifacts/loop/baseline/catalog'
    write_catalog(catalog / 'extra', [CHECK_CASE])
    (catalog / 'extra' / CHECK_CASE).rename(catalog / CHECK_CASE)
    write_trial(tmp_path / 'jobs/baseline-round-1-attempt-1', catalog, CHECK_CASE, 1, [])
    # A trial of a check-set case is refused even when the catalog doesn't list it.
    with pytest.raises(ValueError, match=f'Refusing .*{CHECK_CASE} is outside loop/climb.txt'):
        evidence.build(baseline, tmp_path / 'pack')
    listing = read(catalog / 'catalog.json')
    write(catalog / 'catalog.json', {**listing, 'tasks': [*listing['tasks'], CHECK_CASE]})
    with pytest.raises(ValueError, match=rf"outside loop/climb.txt: \['{CHECK_CASE}'\]"):
        evidence.build(baseline, tmp_path / 'pack')
    assert not (tmp_path / 'pack').exists() and not list(tmp_path.glob('.evidence-*'))


def test_the_trace_analysis_skill_freezes_the_pack_with_its_labels(pack):
    """The pinned Daydream's own cohort.py takes the trial folders and labels.jsonl as they are."""
    if not (ROOT / f'.cache/src/.daydream-{BUILD["source_commit"]}').exists():
        pytest.fail('Fetch the pinned Daydream source first: make setup')
    trials = sorted(pack.glob('trials/*/*'))
    selected = subprocess.run([sys.executable, '-I', str(SKILL / 'scripts/cohort.py'), 'select', *map(str, trials),
                               '--labels', str(pack / 'labels.jsonl')], capture_output=True, text=True)
    assert selected.returncode == 0, selected.stdout + selected.stderr
    manifest = json.loads(selected.stdout)
    assert manifest['errors'] == [] and len(manifest['runs']) == 6
    assert len(manifest['labels']['items']) == len((pack / 'labels.jsonl').read_text().splitlines()) == 18


def first_record(tmp_path: Path) -> Path:
    trial = next((tmp_path / 'jobs/baseline-round-1-attempt-1').glob(f'{CASE_A}__*'))
    return next((trial / 'agent/dataset/runs').glob('*.jsonl'))


@pytest.mark.parametrize('escape', ['/elsewhere/agent/daydream/runs/secret', '../../../../../elsewhere'])
def test_a_run_id_that_is_a_path_is_refused(baseline, tmp_path, escape):
    record = first_record(tmp_path)
    write(record, {**json.loads(record.read_text()), 'run_id': escape})
    with pytest.raises(ValueError, match='not a plain folder name'):
        evidence.build(baseline, tmp_path / 'pack')
    assert not (tmp_path / 'pack').exists() and not list(tmp_path.glob('.evidence-*'))


def test_a_linked_folder_leading_out_of_the_trial_is_refused(baseline, tmp_path):
    # A check-set or held-out trial's evidence, reached through a link inside a climb trial.
    outside = tmp_path / 'jobs/check-round-1/x__y/agent/daydream/runs'
    trial = first_record(tmp_path).parents[3]
    runs = trial / 'agent/daydream/runs'
    run = next(runs.iterdir())
    outside.parent.mkdir(parents=True)
    run.rename(outside.parent / 'runs-moved')
    (outside.parent / 'runs-moved').rename(outside)
    runs.rmdir()
    runs.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='outside the trial'):
        evidence.build(baseline, tmp_path / 'pack')


def test_a_run_whose_findings_were_withheld_keeps_its_evidence_but_gets_no_labels(baseline, tmp_path):
    record = first_record(tmp_path)
    data = json.loads(record.read_text())
    write(record, {**data, 'findings': {'status': 'withheld', 'value': None}})
    index = evidence.build(baseline, tmp_path / 'pack')
    labels = [json.loads(line) for line in (tmp_path / 'pack/labels.jsonl').read_text().splitlines()]
    assert labels and data['run_id'] not in {label['run_id'] for label in labels}
    assert index['totals']['trials'] == 6


def test_an_out_folder_made_meanwhile_is_not_replaced(baseline, tmp_path, monkeypatch):
    real = evidence.collect
    def collect(summary):
        assert (tmp_path / 'pack').is_dir() and not any((tmp_path / 'pack').iterdir())
        return real(summary)
    monkeypatch.setattr(evidence, 'collect', collect)
    evidence.build(baseline, tmp_path / 'pack')
    (tmp_path / 'other').mkdir()
    with pytest.raises(FileExistsError, match='Refusing to replace'):
        evidence.build(baseline, tmp_path / 'other')
