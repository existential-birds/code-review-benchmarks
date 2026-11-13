"""Pooled ReviewBench counts through Harbor's actual custom-metric subprocess, and
the same rewards round by round from the job results Harbor writes."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import uuid

import pytest

from harbor.metrics.uv_script import UvScript
from harbor.models.agent.context import AgentContext
from harbor.models.job.result import JobResult, JobStats
from harbor.models.task.id import LocalTaskId
from harbor.models.trial.config import TaskConfig, TrialConfig
from harbor.models.trial.result import AgentInfo, ExceptionInfo, TrialResult
from harbor.models.verifier.result import VerifierResult

from benchmarks.reviewbench.metric import compute
from benchmarks.reviewbench.report import report

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

    # Harbor pools one job's trials, so a round is a job: one pass over the catalog.
    catalog = tmp_path / 'catalog'
    for number, name in enumerate(('pr-a', 'pr-b'), 1):
        (catalog / name / 'tests').mkdir(parents=True)
        (catalog / name / 'tests/task.json').write_text(json.dumps({'task_id': number, 'evaluation_id': 2}))
    (catalog / 'catalog.json').write_text(json.dumps({'reviewbench_revision': 'r', 'split': 'test', 'judge': 'j',
                                                      'tasks': ['pr-a', 'pr-b']}))
    first, second = counts(4, 2, 2, 1, 4, 2), counts(6, 4, 1, 0, 6, 1)
    def job(name, build, policy, rewards, raised=()):
        """A job as Harbor's own models write it, with the review's record in each trial's agent logs."""
        trials = []
        for task, overall in rewards.items():
            trial = TrialResult(
                task_name=task, trial_name=f'{task}__{name}', trial_uri='', task_checksum='',
                task_id=LocalTaskId(path=catalog / task), config=TrialConfig(task=TaskConfig(path=catalog / task)),
                agent_info=AgentInfo(name='daydream', version='0.34.0'), agent_result=AgentContext(n_input_tokens=120),
                verifier_result=overall and VerifierResult(rewards=dict(reward(overall), rb_task=int(task == 'pr-b') + 1)),
                exception_info=ExceptionInfo.from_exception(TimeoutError()) if task in raised else None)
            trials.append(trial)
            folder = tmp_path / name / trial.trial_name
            (folder / 'agent').mkdir(parents=True)
            (folder / 'result.json').write_text(trial.model_dump_json())
            (folder / 'agent/execution.json').write_text(json.dumps({
                'status': 'complete' if overall else 'incomplete', 'omitted_findings': 2 if task == 'pr-a' else 0,
                'elapsed_seconds': 10, 'usage': {'cost_usd': .5} if policy else {}, 'build': {'source_commit': build},
                'assignment_policy': policy, 'committed_review': policy and {'assignment_policy': {
                    'name': policy, 'decomposed': policy == 'adaptive'}}}))
        stats = JobStats.from_trial_results(trials)
        for group in stats.evals.values():
            group.metrics = [result]
        (tmp_path / name / 'result.json').write_text(JobResult(
            id=uuid.uuid4(), started_at=datetime.now(timezone.utc), n_total_trials=len(trials),
            stats=stats).model_dump_json(exclude={'trial_results'}))
        return tmp_path / name
    pinned = [job('pinned-1', 'p' * 40, None, {'pr-a': first, 'pr-b': second}),
              job('pinned-2', 'p' * 40, None, {'pr-a': second, 'pr-b': first})]
    adaptive = [job('adaptive-1', 'c' * 40, 'adaptive', {'pr-a': first, 'pr-b': second}),
                job('adaptive-2', 'c' * 40, 'adaptive', {'pr-a': first, 'pr-b': None})]
    # As in ReviewBench's own rounds, a trial that raised is not scored, whatever reward it left.
    broad = [job('broad-1', 'c' * 40, 'broad', {'pr-a': second, 'pr-b': first}, raised={'pr-b'})]
    compared = report(catalog, [pinned, adaptive, broad])
    assert compared['catalog'] == {'reviewbench_revision': 'r', 'split': 'test', 'judge': 'j'}
    # A side is the build and review its trials recorded, and each block names its review cases.
    whole, broken, single = (compared['sides'][side] for side in (
        'p' * 40 + '/review', 'c' * 40 + '/adaptive', 'c' * 40 + '/broad'))
    assert len(compared['sides']) == 3
    assert {name: block['tasks'] for name, block in compared['blocks'].items()} == {
        'all': ['pr-a', 'pr-b'], 'matched': ['pr-a'], 'decomposed': ['pr-a']}
    assert 'reduced set' in compared['blocks']['matched']['meaning']
    # ReviewBench's own round metric: each complete round, then its mean over the rounds.
    assert whole['all']['complete'] == 1 and whole['all']['completed_rounds'] == 2
    assert whole['all']['round.1.micro.overall.grounded_precision'] == pytest.approx(3 / 6)
    assert whole['all']['round.1.macro.overall.grounded_precision'] == pytest.approx((2 / 2 + 1 / 4) / 2)
    assert whole['all']['summary.micro.overall.grounded_recall.mean'] == pytest.approx(3 / 10)
    assert whole['all']['summary.micro.overall.grounded_recall.defined_rounds'] == 2
    assert whole['rounds'][0] | {'trials': None} == {
        'job': 'pinned-1', 'expected': 2, 'missing': 0, 'produced': 2, 'completed_empty': 0, 'incomplete': 0,
        'failed': 0, 'evaluated': 2, 'conversion_limited': 1, 'omitted_findings': 2, 'decomposed': 0,
        'elapsed_seconds': 20, 'cost_usd': None, 'trials': None,
        # Harbor's own totals and pooled metric for the job, as it wrote them.
        'harbor': {'n_completed_trials': 2, 'n_errored_trials': 0, 'n_cancelled_trials': 0, 'n_input_tokens': 240,
                   'n_cache_tokens': None, 'n_output_tokens': None, 'metrics': [result]}}
    assert whole['rounds'][0]['trials'][0] == {
        'task': 'pr-a', 'trial': 'pr-a__pinned-1', 'status': 'complete', 'exception': None,
        'reward': dict(reward(first), rb_task=1), 'ratios': {
            'grounded_precision': 1, 'grounded_recall': 2 / 4, 'augmented_precision': 3 / 4,
            'augmented_recall': 3 / 5, 'novel_tp_yield': 1 / 4},
        'omitted_findings': 2, 'elapsed_seconds': 10, 'usage': {}, 'side': 'p' * 40 + '/review',
        'assignment_policy': None}
    # One trial without a score withholds that round's ratios and every average.
    assert broken['all']['complete'] == 0 and broken['all']['failed_trials'] == 1
    assert 'round.1.micro.overall.grounded_precision' in broken['all']
    assert not [key for key in broken['all'] if key.startswith(('summary.', 'round.2.micro', 'round.2.macro'))]
    assert broken['rounds'][1] | {'trials': None, 'harbor': None} == {
        'job': 'adaptive-2', 'expected': 2, 'missing': 0, 'produced': 1, 'completed_empty': 0, 'incomplete': 1,
        'failed': 0, 'evaluated': 1, 'conversion_limited': 1, 'omitted_findings': 2, 'decomposed': 2,
        'elapsed_seconds': 20, 'cost_usd': 1.0, 'trials': None, 'harbor': None}
    assert broken['rounds'][1]['trials'][1] == {
        'task': 'pr-b', 'trial': 'pr-b__adaptive-2', 'status': 'incomplete', 'exception': None,
        'reward': None, 'ratios': None, 'omitted_findings': 0, 'elapsed_seconds': 10, 'usage': {'cost_usd': .5},
        'side': 'c' * 40 + '/adaptive', 'assignment_policy': {'name': 'adaptive', 'decomposed': True}}
    assert (single['rounds'][0]['evaluated'], single['all']['complete'], single['all']['failed_trials']) == (1, 0, 1)
    assert single['rounds'][0]['trials'][1]['exception'] == 'TimeoutError'
    assert single['rounds'][0]['harbor']['n_errored_trials'] == 1
    # Over the review cases every job scored, every side is complete and comparable.
    for side in compared['sides'].values():
        assert side['matched']['complete'] == 1 and side['matched']['expected_prs'] == 1
        assert (side['all']['full_dataset'], side['matched']['full_dataset']) == (1, 0)
        # Only a case every adaptive review split into assignments is an adaptive data point.
        assert side['decomposed'] == side['matched']
    assert whole['matched']['summary.micro.overall.grounded_precision.mean'] == pytest.approx((1 + 1 / 4) / 2)
    assert broken['matched']['summary.micro.overall.grounded_precision.mean'] == 1
    # A trial that left no record never reached the review, and its job keeps its side; a
    # review case a job never ran is missing; an adaptive review that stayed whole isn't adaptive.
    (tmp_path / 'adaptive-2/pr-b__adaptive-2/agent/execution.json').unlink()
    shutil.rmtree(tmp_path / 'broad-1/pr-b__broad-1')
    whole_review = tmp_path / 'adaptive-1/pr-a__adaptive-1/agent/execution.json'
    whole_review.write_text(whole_review.read_text().replace('"decomposed": true', '"decomposed": false'))
    again = report(catalog, [pinned, adaptive, broad])
    unreached = again['sides']['c' * 40 + '/adaptive']['rounds'][1]
    assert (unreached['failed'], unreached['incomplete'], unreached['produced']) == (1, 0, 1)
    assert unreached['trials'][1]['status'] == 'failed' and unreached['trials'][1]['side'] is None
    assert again['sides']['c' * 40 + '/broad']['rounds'][0]['missing'] == 1
    assert again['blocks']['matched']['tasks'] == ['pr-a'] and again['blocks']['decomposed']['tasks'] == []
    assert again['sides']['p' * 40 + '/review']['decomposed'] == {}
    # Every side needs one build and review of its own, and every job one attempt per review case.
    for sides in ([pinned, pinned], [[pinned[0], adaptive[0]]], [[]]):
        with pytest.raises(ValueError, match='one Daydream build and review of its own'):
            report(catalog, sides)
    shutil.copytree(tmp_path / 'pinned-1/pr-a__pinned-1', tmp_path / 'pinned-1/pr-a__again')
    for jobs in (pinned, [job('another-catalog', 'p' * 40, None, {'pr-c': first})]):
        with pytest.raises(ValueError, match='not one pass'):
            report(catalog, [jobs])


def test_undefined_ratios_stay_undefined_and_invalid_rewards_fail():
    empty = compute([reward(counts(0, 0, 0, 0, 0, 0))])
    assert empty['scored_trials'] == 1 and empty['candidate_count'] == 0
    assert not {'grounded_precision', 'grounded_recall', 'grounded_f1'} & set(empty)
    assert compute([None]) == {'total_trials': 1, 'scored_trials': 0, 'missing_trials': 1}
    with pytest.raises(ValueError, match='Inconsistent counts'):
        compute([reward(dict(counts(2, 1, 1, 0, 1, 1), matched_tp_count=2))])
    with pytest.raises(ValueError, match='not a ReviewBench reward'):
        compute([{'rb_schema': 2}])
