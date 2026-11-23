"""loop.rounds over job folders written with Harbor's own models, and a stand-in harbor run."""
from datetime import datetime, timezone
import json
from pathlib import Path
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


def write_job(job: Path, catalog: Path, rewards: dict, finished=True) -> None:
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
                'usage': {'cost_usd': 2.5}, 'build': {'source_commit': BUILD}}))
    (job / 'result.json').write_text(JobResult(
        id=uuid.uuid4(), started_at=datetime.now(timezone.utc), n_total_trials=len(trials),
        finished_at=datetime.now(timezone.utc) if finished else None,
        stats=JobStats.from_trial_results(trials)).model_dump_json(exclude={'trial_results'}))


def write_catalog(catalog: Path, names: list[str]) -> None:
    for number, name in enumerate(names, 1):
        (catalog / name / 'tests').mkdir(parents=True)
        (catalog / name / 'tests/task.json').write_text(json.dumps({'task_id': number, 'evaluation_id': 7}))
    (catalog / 'catalog.json').write_text(json.dumps(
        {'reviewbench_revision': 'r', 'split': 'dev', 'judge': 'j', 'tasks': names}))


def test_summary_scores_each_round_pools_its_trials_and_keeps_every_cases_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(rounds, 'JOBS', tmp_path / 'jobs')
    catalog = tmp_path / 'catalog'
    write_catalog(catalog, ['case-a', 'case-b'])
    first, second = counts(4, 2, 2, 1, 4, 2), counts(6, 4, 1, 0, 6, 1)
    write_job(tmp_path / 'jobs/r1', catalog, {'case-a': first, 'case-b': second})
    write_job(tmp_path / 'jobs/r2', catalog, {'case-a': second, 'case-b': None})
    summary = rounds.summarise(catalog, ['r1', 'r2'])
    assert summary['side'] == BUILD
    one, two = summary['rounds']
    # Round 1 pools both cases' counts: 3 of 6 matched are true, 3 of 10 golden covered, 4 of 10 sent valid.
    assert (one['grounded_precision'], one['grounded_recall'], one['grounded_f1']) == pytest.approx((.5, .3, .375))
    assert (one['augmented_precision'], one['augmented_recall']) == pytest.approx((4 / 10, 4 / 11))
    assert (one['findings_sent'], one['unmatched_judged_wrong'], one['n_input_tokens']) == (10, 3, 200)
    assert (one['expected'], one['evaluated'], one['failed'], one['incomplete']) == (2, 2, 0, 0)
    assert (one['omitted_findings'], one['elapsed_seconds'], one['cost_usd']) == (2, 120, 5.0)
    # Round 2 scored case-a only; the failed trial leaves the pooled counts and is counted.
    assert (two['grounded_precision'], two['grounded_f1'], two['evaluated'], two['failed']) == pytest.approx(
        (.25, .2, 1, 1))
    f1 = summary['scores']['grounded_f1']
    assert f1['defined_rounds'] == 2 and f1['mean'] == pytest.approx(.2875)
    assert f1['stddev'] == pytest.approx(.175 / 2 ** .5)
    assert summary['cases'] == {'case-a': [first, second], 'case-b': [second, None]}


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
    (tmp_path / 'cases.txt').write_text('# header\ncase-a\ncase-b\n')
    return tmp_path


def test_a_case_outside_the_dev_split_is_refused(workspace):
    (workspace / 'held-out.txt').write_text('case-a\ncase-t\n')
    with pytest.raises(ValueError, match=r"Unknown ReviewBench task names in the dev set: \['case-t'\]"):
        rounds.run('pinned', workspace / 'held-out.txt', 1, 'refused', upstream=workspace)
    assert not (workspace / 'artifacts/loop/refused/summary.json').exists()


def test_rerun_skips_finished_rounds_and_never_reopens_an_unfinished_job(workspace, monkeypatch):
    outcomes, calls = [], []
    def harbor(command, check):
        job, catalog = command[command.index('--job-name') + 1], Path(command[command.index('-p') + 1])
        calls.append(job)
        assert command[command.index('-k') + 1] == '1' and command[command.index('-r') + 1] == '0'
        outcome = outcomes.pop(0)
        write_job(Path('jobs') / job, catalog, {'case-a': counts(2, 1, 1, 0, 2, 1), 'case-b': counts(2, 2, 1, 0, 2, 1)},
                  finished=outcome == 'finish')
        if outcome == 'crash':
            raise KeyboardInterrupt
    monkeypatch.setattr(rounds, 'subprocess', SimpleNamespace(run=harbor))
    summary = workspace / 'artifacts/loop/base/summary.json'

    outcomes[:] = ['finish', 'die']
    with pytest.raises(RuntimeError, match='Round 2 did not finish'):
        rounds.run('pinned', workspace / 'cases.txt', 2, 'base', concurrency=2)
    outcomes[:] = ['finish']
    rounds.run('pinned', workspace / 'cases.txt', 2, 'base')
    outcomes[:] = ['crash']
    with pytest.raises(KeyboardInterrupt):
        rounds.run('pinned', workspace / 'cases.txt', 3, 'base')
    outcomes[:] = ['finish']
    state = rounds.run('pinned', workspace / 'cases.txt', 3, 'base')
    assert rounds.run('pinned', workspace / 'cases.txt', 3, 'base') == state

    assert calls == ['base-round-1-attempt-1', 'base-round-2-attempt-1', 'base-round-2-attempt-2',
                     'base-round-3-attempt-1', 'base-round-3-attempt-2']
    assert [(entry['job'][5:], entry['state']) for entry in json.loads(summary.read_text())['jobs']] == [
        ('round-1-attempt-1', 'finished'), ('round-2-attempt-1', 'abandoned'), ('round-2-attempt-2', 'finished'),
        ('round-3-attempt-1', 'abandoned'), ('round-3-attempt-2', 'finished')]
    assert [entry['job'] for entry in state['rounds']] == [
        'base-round-1-attempt-1', 'base-round-2-attempt-2', 'base-round-3-attempt-2']
    with pytest.raises(ValueError, match='already scored'):
        rounds.run('pinned', workspace / 'cases.txt', 3, 'base', limit=1)
