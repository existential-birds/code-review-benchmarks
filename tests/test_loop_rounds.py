"""loop.rounds over job folders written with Harbor's own models, and a stand-in harbor run."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

import pytest

from harbor.models.agent.context import AgentContext
from harbor.models.job.result import JobResult, JobStats
from harbor.models.task.id import LocalTaskId
from harbor.models.trial.config import TaskConfig, TrialConfig
from harbor.models.trial.result import AgentInfo, ExceptionInfo, TrialResult
from harbor.models.verifier.result import VerifierResult

from benchmarks.reviewbench import convert
from loop import rounds
from test_reviewbench import exported_task

BUILD = 'p' * 40


def counts(candidates, matched, matched_tp, unmatched_tp, golden_tp, covered):
    return dict(candidate_count=candidates, golden_tp_count=golden_tp, matched_count=matched,
                matched_tp_count=matched_tp, unmatched_count=candidates - matched,
                unmatched_tp_count=unmatched_tp, unmatched_fp_count=candidates - matched - unmatched_tp,
                golden_covered_count=covered)


def write_job(job: Path, catalog: Path, rewards: dict, finished=True, build=BUILD) -> None:
    """A job as Harbor writes it. A null reward is a trial that raised and left no review record."""
    expected = {name: json.loads((catalog / name / 'tests/task.json').read_text()) for name in rewards}
    trials = []
    for task, overall in rewards.items():
        trial = TrialResult(
            task_name=task, trial_name=f'{task}__{job.name}', trial_uri='', task_checksum='',
            task_id=LocalTaskId(path=catalog / task), config=TrialConfig(task=TaskConfig(path=catalog / task)),
            agent_info=AgentInfo(name='daydream', version='0.37.0'), agent_result=AgentContext(n_input_tokens=100),
            verifier_result=overall and VerifierResult(rewards={
                'rb_schema': 1, 'rb_task': expected[task]['task_id'], 'rb_evaluation': expected[task]['evaluation_id'],
                **{f'overall.{key}': value for key, value in overall.items()}}),
            exception_info=None if overall else ExceptionInfo.from_exception(TimeoutError()),
            finished_at=datetime.now(timezone.utc), verifier_environment_mode='separate')
        trials.append(trial)
        (job / trial.trial_name / 'agent').mkdir(parents=True)
        (job / trial.trial_name / 'result.json').write_text(trial.model_dump_json())
        if overall:
            (job / trial.trial_name / 'agent/execution.json').write_text(json.dumps({
                'status': 'complete', 'omitted_findings': 1, 'elapsed_seconds': 60,
                'usage': {'cost_usd': 2.5}, 'build': {'source_commit': build}}))
    (job / 'result.json').write_text(JobResult(
        id=uuid.uuid4(), started_at=datetime.now(timezone.utc), n_total_trials=len(trials),
        finished_at=datetime.now(timezone.utc) if finished else None,
        stats=JobStats.from_trial_results(trials)).model_dump_json(exclude={'trial_results'}))


def write_catalog(catalog: Path, names: list[str]) -> None:
    for number, name in enumerate(names, 1):
        (catalog / name / 'tests').mkdir(parents=True)
        (catalog / name / 'tests/task.json').write_text(json.dumps({'task_id': number, 'evaluation_id': 7}))
    (catalog / 'catalog.json').write_text(json.dumps(
        {'reviewbench_revision': 'r', 'split': 'dev', 'judge': 'j', 'daydream': {'version': 'v'}, 'tasks': names}))


def test_summary_scores_each_round_pools_its_trials_and_keeps_every_cases_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(rounds, 'JOBS', tmp_path / 'jobs')
    catalog = tmp_path / 'catalog'
    write_catalog(catalog, ['case-a', 'case-b'])
    first, second = counts(4, 2, 2, 1, 4, 2), counts(6, 4, 1, 0, 6, 1)
    write_job(tmp_path / 'jobs/r1', catalog, {'case-a': first, 'case-b': second})
    write_job(tmp_path / 'jobs/r2', catalog, {'case-a': second, 'case-b': None})
    summary = rounds.summarise(catalog, ['r1', 'r2'])
    assert summary['side'] == BUILD
    assert summary['catalog_identity'] == {'reviewbench_revision': 'r', 'split': 'dev', 'judge': 'j',
                                           'daydream': {'version': 'v'}}
    one, two = summary['rounds']
    # Round 1 pools both cases' counts: 3 of 6 matched are true, 3 of 10 golden covered, 4 of 10 sent valid.
    assert (one['grounded_precision'], one['grounded_recall'], one['grounded_f1']) == pytest.approx((.5, .3, .375))
    assert (one['augmented_precision'], one['augmented_recall']) == pytest.approx((4 / 10, 4 / 11))
    assert (one['findings_sent'], one['unmatched_judged_wrong'], one['n_input_tokens']) == (10, 3, 200)
    assert (one['expected'], one['missing'], one['evaluated'], one['failed'], one['incomplete']) == (2, 0, 2, 0, 0)
    assert (one['omitted_findings'], one['elapsed_seconds'], one['cost_usd']) == (2, 120, 5.0)
    # Round 2 scored case-a only; the failed trial leaves the pooled counts and is counted.
    assert (two['grounded_precision'], two['grounded_f1'], two['evaluated'], two['failed']) == pytest.approx(
        (.25, .2, 1, 1))
    f1 = summary['scores']['grounded_f1']
    assert f1['defined_rounds'] == 2 and f1['mean'] == pytest.approx(.2875)
    assert f1['stddev'] == pytest.approx(.175 / 2 ** .5)
    assert summary['cases'] == {'case-a': [first, second], 'case-b': [second, None]}
    # Rounds that failed before any review record no build, and still count their failures.
    write_job(tmp_path / 'jobs/down', catalog, {'case-a': None, 'case-b': None})
    down = rounds.summarise(catalog, ['down'])
    assert down['side'] is None and down['rounds'][0]['failed'] == 2 and down['scores']['grounded_f1']['mean'] is None
    assert down['rounds'][0]['findings_sent'] is None
    write_job(tmp_path / 'jobs/other', catalog, {'case-a': first}, build='o' * 40)
    with pytest.raises(ValueError, match='One build per summary'):
        rounds.summarise(catalog, ['r1', 'other'])


@pytest.fixture
def workspace(tmp_path, monkeypatch, pinned_wheel):
    """A repository root with a stand-in ReviewBench exporter whose test set is case-t."""
    monkeypatch.chdir(tmp_path)
    exported = {'full': ['case-a', 'case-b', 'case-t'], 'test': ['case-t']}
    def export(upstream, destination, split):
        for name in exported[split]:
            exported_task(destination / name, name)
            (destination / name / 'tests/task.json').write_text(
                json.dumps({'task_id': exported['full'].index(name) + 1, 'evaluation_id': 7}))
        (destination / 'dataset.toml').write_text('')
        (destination / 'metric.py').write_text('')
    monkeypatch.setattr(convert, 'check_source', lambda upstream: None)
    monkeypatch.setattr(convert, 'export', export)
    for name in rounds.LOGINS:
        (tmp_path / name).mkdir()
        monkeypatch.setenv(name, str(tmp_path / name))
    (tmp_path / 'cases.txt').write_text('# header\ncase-a\ncase-b\n')
    return tmp_path


def test_a_case_outside_the_dev_split_is_refused(workspace, monkeypatch):
    (workspace / 'held-out.txt').write_text('case-a\ncase-t\n')
    with pytest.raises(ValueError, match=r"Unknown ReviewBench task names in the dev set: \['case-t'\]"):
        rounds.run('pinned', workspace / 'held-out.txt', 1, 'refused', upstream=workspace)
    assert not (workspace / 'artifacts/loop/refused/summary.json').exists()
    # Without the login folders every trial would fail at once, so nothing starts.
    monkeypatch.delenv('JUDGE_PI_AUTH_DIR')
    with pytest.raises(ValueError, match='Set JUDGE_PI_AUTH_DIR'):
        rounds.run('pinned', workspace / 'cases.txt', 1, 'no-login', upstream=workspace)
    assert not (workspace / 'artifacts/loop/no-login').exists()


SCORED = {'case-a': counts(2, 1, 1, 0, 2, 1), 'case-b': counts(2, 2, 1, 0, 2, 1)}


def test_rerun_skips_finished_rounds_and_never_reopens_an_unfinished_or_failed_job(workspace, monkeypatch):
    outcomes, calls = [], []
    def harbor(command, check):
        job, catalog = command[command.index('--job-name') + 1], Path(command[command.index('-p') + 1])
        calls.append(job)
        assert command[command.index('-k') + 1] == '1' and command[command.index('-r') + 1] == '0'
        outcome = outcomes.pop(0)
        rewards = {'case-a': None, 'case-b': None} if outcome == 'fail' else SCORED
        write_job(Path('jobs') / job, catalog, rewards, finished=outcome in ('finish', 'fail', 'finish-then-crash'))
        if outcome in ('crash', 'finish-then-crash'):
            raise KeyboardInterrupt
    monkeypatch.setattr(rounds, 'subprocess', SimpleNamespace(run=harbor))
    summary = workspace / 'artifacts/loop/base/summary.json'
    cases = workspace / 'cases.txt'

    for planned, rounds_asked, error in ((['finish', 'die'], 2, 'Round 2 abandoned'), (['finish'], 2, None),
                                         (['crash'], 3, KeyboardInterrupt), (['fail'], 3, 'Round 3 failed'),
                                         (['finish-then-crash'], 4, KeyboardInterrupt), (['finish'], 4, None)):
        outcomes[:] = planned
        if error is None:
            state = rounds.run('pinned', cases, rounds_asked, 'base', concurrency=2)
        elif error is KeyboardInterrupt:
            with pytest.raises(KeyboardInterrupt):
                rounds.run('pinned', cases, rounds_asked, 'base')
        else:
            with pytest.raises(RuntimeError, match=error):
                rounds.run('pinned', cases, rounds_asked, 'base')
        assert not outcomes
    assert rounds.run('pinned', cases, 4, 'base') == state and rounds.run('pinned', cases, 2, 'base') == state

    # The job that finished before its command was killed counts; the dead, crashed and failed ones never rerun.
    assert [(entry['job'][5:], entry['state']) for entry in json.loads(summary.read_text())['jobs']] == [
        ('round-1-attempt-1', 'finished'), ('round-2-attempt-1', 'abandoned'), ('round-2-attempt-2', 'finished'),
        ('round-3-attempt-1', 'abandoned'), ('round-3-attempt-2', 'failed'), ('round-3-attempt-3', 'finished'),
        ('round-4-attempt-1', 'finished')]
    assert calls == [entry['job'] for entry in state['jobs']]
    assert [entry['job'] for entry in state['rounds']] == [
        'base-round-1-attempt-1', 'base-round-2-attempt-2', 'base-round-3-attempt-3', 'base-round-4-attempt-1']
    with pytest.raises(ValueError, match='already scored'):
        rounds.run('pinned', cases, 3, 'base', limit=1)


def test_a_pilot_scores_only_its_first_cases_and_one_name_runs_once(workspace, monkeypatch):
    monkeypatch.setattr(rounds, 'subprocess', SimpleNamespace(run=lambda command, check: write_job(
        Path('jobs') / command[command.index('--job-name') + 1], Path(command[command.index('-p') + 1]),
        {'case-a': SCORED['case-a']})))
    state = rounds.run('pinned', workspace / 'cases.txt', 1, 'pilot', limit=1)
    assert json.loads((workspace / 'artifacts/loop/pilot/catalog/catalog.json').read_text())['tasks'] == ['case-a']
    assert list(state['cases']) == ['case-a'] and state['rounds'][0]['evaluated'] == 1
    with open(workspace / 'artifacts/loop/pilot/lock') as held:
        rounds.fcntl.flock(held, rounds.fcntl.LOCK_EX)
        with pytest.raises(RuntimeError, match='Another loop.rounds is scoring pilot'):
            rounds.run('pinned', workspace / 'cases.txt', 2, 'pilot', limit=1)
    # A job folder already there, from an earlier summary that was lost, is never reopened.
    (workspace / 'jobs/pilot-round-2-attempt-1').mkdir()
    with pytest.raises(FileExistsError, match='Refusing to reopen'):
        rounds.run('pinned', workspace / 'cases.txt', 2, 'pilot', limit=1)
    for bad in ({'rounds': 0}, {'limit': 0}, {'concurrency': 0}):
        with pytest.raises(ValueError, match='must be positive'):
            rounds.run('pinned', workspace / 'cases.txt', bad.get('rounds', 1), 'other',
                       limit=bad.get('limit'), concurrency=bad.get('concurrency'))


def argument(command, flag):
    return command[command.index(flag) + 1]


@pytest.fixture
def aacr_workspace(workspace, monkeypatch):
    """The workspace with a stand-in AACR source of three cases, owner-project-3 held out."""
    from benchmarks.aacr import convert as aacr_convert
    records = [{'githubPrUrl': f'https://github.com/owner/project/pull/{number}', 'project_main_language': 'Go',
                'source_commit': 'a' * 40, 'target_commit': 'b' * 40,
                'comments': [{'path': 'main.go', 'note': 'verifier only reference', 'from_line': 1, 'to_line': 1}]}
               for number in (1, 2, 3)]
    def write_task(record, output, daydream, build_file):
        assert daydream and build_file is None
        task = output / aacr_convert.task_name(record)
        (task / 'tests').mkdir(parents=True)
        (task / 'tests/build.json').write_text(json.dumps({'source_commit': BUILD}))
        return task
    (workspace / 'holdout.txt').write_text('# held out\nowner-project-3\n')
    monkeypatch.setattr(aacr_convert, 'HOLDOUT', workspace / 'holdout.txt')
    monkeypatch.setattr(aacr_convert, 'load_records', lambda upstream: records)
    monkeypatch.setattr(aacr_convert, 'write_task', write_task)
    (workspace / 'aacr-cases.txt').write_text('owner-project-1\nowner-project-2\n')
    return workspace


def test_aacr_rounds_give_a_summary_of_the_same_shape_from_aacrs_report(aacr_workspace, monkeypatch):
    from test_aacr_report import write_job as write_aacr_job
    outcomes = [{'owner-project-1': (4, 2, 1, 1), 'owner-project-2': (2, 3, 2, 2)},
                {'owner-project-1': (4, 4, 2, 2), 'owner-project-2': (2, 0, 0, 0)}]
    commands = []
    def harbor(command, check):
        commands.append(command)
        write_aacr_job(Path('jobs') / argument(command, '--job-name'), Path(argument(command, '-p')),
                       outcomes.pop(0), BUILD)
    monkeypatch.setattr(rounds, 'subprocess', SimpleNamespace(run=harbor))
    monkeypatch.setattr(sys, 'argv', ['loop.rounds', '--benchmark', 'aacr', '--build', 'pinned',
                                      '--cases', 'aacr-cases.txt', '--rounds', '2', '--name', 'aacr',
                                      '--concurrency', '1'])
    rounds.main()
    assert [argument(command, '-c') for command in commands] == ['experiments/aacr-baseline.yaml'] * 2
    summary = json.loads((aacr_workspace / 'artifacts/loop/aacr/summary.json').read_text())
    assert summary['request'] == {'build': 'pinned', 'cases': ['owner-project-1', 'owner-project-2'],
                                  'benchmark': 'aacr'}
    assert summary['catalog_identity'] == {'aacr_revision': rounds.aacr_convert.AACR_REVISION,
                                           'dataset_sha256': rounds.aacr_convert.DATASET_SHA256, 'split': 'dev',
                                           'daydream': {'source_commit': BUILD}}
    assert summary['side'] == BUILD
    one, two = summary['rounds']
    assert (one['expected'], one['missing'], one['evaluated'], one['failed'], one['incomplete']) == (2, 0, 2, 0, 0)
    assert (one['findings_sent'], one['elapsed_seconds'], one['cost_usd'], one['n_input_tokens']) == (5, 40, .5, 200)
    assert (one['file_semantic_match_rate'], one['file_semantic_recall_rate'], one['balanced_score']) == (.6, .5, .545)
    assert (two['completed_empty'], two['findings_sent'], two['balanced_score']) == (1, 4, .4)
    assert summary['scores']['balanced_score'] == pytest.approx(
        {'defined_rounds': 2, 'mean': .4725, 'stddev': .145 / 2 ** .5})
    counts = ('expected_notes', 'generated_notes', 'matched_generated_notes', 'matched_file_semantic_notes')
    assert summary['cases'] == {'owner-project-1': [dict(zip(counts, (4, 2, 1, 1))), dict(zip(counts, (4, 4, 2, 2)))],
                                'owner-project-2': [dict(zip(counts, (2, 3, 2, 2))), dict(zip(counts, (2, 0, 0, 0)))]}


def test_aacr_rounds_refuse_a_held_out_case_and_a_failed_conversion_leaves_no_catalog(aacr_workspace, monkeypatch):
    from benchmarks.aacr import convert as aacr_convert
    from test_aacr_report import write_job as write_aacr_job
    # Every catalog is dev: a held-out case is refused before anything is written, and nothing asks for another split.
    (aacr_workspace / 'held-out.txt').write_text('owner-project-1\nowner-project-3\n')
    with pytest.raises(ValueError, match=r"Unknown AACR task names in the dev set: \['owner-project-3'\]"):
        rounds.run('pinned', aacr_workspace / 'held-out.txt', 1, 'refused', benchmark='aacr')
    assert not (aacr_workspace / 'artifacts/loop/refused/catalog').exists()
    with pytest.raises(TypeError):
        rounds.run('pinned', aacr_workspace / 'aacr-cases.txt', 1, 'held-out', benchmark='aacr', split='holdout')
    monkeypatch.setattr(sys, 'argv', ['loop.rounds', '--benchmark', 'aacr', '--split', 'holdout', '--build',
                                      'pinned', '--cases', 'aacr-cases.txt', '--rounds', '1', '--name', 'cli'])
    with pytest.raises(SystemExit):
        rounds.main()

    # A case whose commit is gone fails to convert; the catalog it half wrote is removed, so the name runs again.
    write_task = aacr_convert.write_task
    def unavailable(record, output, daydream, build_file):
        if aacr_convert.task_name(record) == 'owner-project-2':
            raise RuntimeError('commit unavailable')
        return write_task(record, output, daydream, build_file)
    monkeypatch.setattr(aacr_convert, 'write_task', unavailable)
    cases = aacr_workspace / 'aacr-cases.txt'
    with pytest.raises(ValueError, match=r"failed to convert: \{'owner-project-2': 'RuntimeError: commit unavailable'"
                                         r"\}\. Drop them from the case list"):
        rounds.run('pinned', cases, 1, 'gone', benchmark='aacr')
    assert not (aacr_workspace / 'artifacts/loop/gone/catalog').exists()
    assert not (aacr_workspace / 'artifacts/loop/gone/summary.json').exists()
    monkeypatch.setattr(rounds, 'subprocess', SimpleNamespace(run=lambda command, check: write_aacr_job(
        Path('jobs') / argument(command, '--job-name'), Path(argument(command, '-p')),
        {'owner-project-1': (3, 1, 1, 1)}, BUILD)))
    cases.write_text('owner-project-1\n')
    state = rounds.run('pinned', cases, 1, 'gone', benchmark='aacr')
    assert state['rounds'][0]['evaluated'] == 1 and list(state['cases']) == ['owner-project-1']


def test_a_reviewbench_summary_from_before_aacr_rounds_still_resumes(workspace, monkeypatch):
    calls = []
    monkeypatch.setattr(rounds, 'subprocess', SimpleNamespace(run=lambda command, check: calls.append(command)
                                                              or write_job(Path('jobs') / argument(command, '--job-name'),
                                                                           Path(argument(command, '-p')), SCORED)))
    first = rounds.run('pinned', workspace / 'cases.txt', 1, 'noise', concurrency=2)
    assert first['request'] == {'build': 'pinned', 'cases': ['case-a', 'case-b']}
    assert calls[0][1:] == ['run', '-c', 'experiments/reviewbench.yaml', '-p', 'artifacts/loop/noise/catalog',
                            '-k', '1', '-r', '0', '-n', '2', '--job-name', 'noise-round-1-attempt-1']
    resumed = rounds.run('pinned', workspace / 'cases.txt', 2, 'noise', concurrency=2)
    assert len(calls) == 2 and [entry['round'] for entry in resumed['rounds']] == [1, 2]
