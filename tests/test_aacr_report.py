"""AACR rounds from job folders written with Harbor's own models, scored by AACR's own metric."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import uuid

import pytest

from harbor.models.agent.context import AgentContext
from harbor.models.job.result import JobResult, JobStats
from harbor.models.task.id import LocalTaskId
from harbor.models.trial.config import TaskConfig, TrialConfig
from harbor.models.trial.result import AgentInfo, ExceptionInfo, TrialResult
from harbor.models.verifier.result import VerifierResult

from benchmarks.aacr.metric import COUNTS, compute
from benchmarks.aacr.report import RATES, report

PINNED, CANDIDATE = 'p' * 40, 'c' * 40


def reward(expected, generated, matched_generated, matched_references):
    """A verifier reward as score.py writes it: one case's rates from AACR's evaluator, and its four counts."""
    counts = dict(zip(COUNTS, (expected, generated, matched_generated, matched_references)))
    return {**{key: compute([counts])[key] for key in RATES}, **counts}


def write_job(job: Path, catalog: Path, trials: dict, build: str, finished=True) -> Path:
    """A job as Harbor writes it, with the review's record in each trial's agent logs.

    Each case maps to its reward counts, 'incomplete' for a review that didn't finish its
    analysis and left no reward, or 'raised' for a trial that raised before any review.
    """
    results = []
    for task, outcome in trials.items():
        scored = isinstance(outcome, tuple)
        trial = TrialResult(
            task_name=task, trial_name=f'{task}__{job.name}', trial_uri='', task_checksum='',
            task_id=LocalTaskId(path=catalog / task), config=TrialConfig(task=TaskConfig(path=catalog / task)),
            agent_info=AgentInfo(name='daydream', version='0.37.0'), agent_result=AgentContext(n_input_tokens=100),
            verifier_result=VerifierResult(rewards=reward(*outcome)) if scored else None,
            exception_info=ExceptionInfo.from_exception(TimeoutError()) if outcome == 'raised' else None,
            finished_at=datetime.now(timezone.utc), verifier_environment_mode='separate')
        results.append(trial)
        (job / trial.trial_name / 'agent').mkdir(parents=True)
        (job / trial.trial_name / 'result.json').write_text(trial.model_dump_json())
        if outcome != 'raised':
            status = 'incomplete' if not scored else 'complete' if outcome[1] else 'completed_empty'
            (job / trial.trial_name / 'agent/execution.json').write_text(json.dumps({
                'status': status, 'elapsed_seconds': 30 if task == 'case-a' else 20,
                'usage': {'cost_usd': .25} if scored else {}, 'build': {'source_commit': build}}))
    (job / 'result.json').write_text(JobResult(
        id=uuid.uuid4(), started_at=datetime.now(timezone.utc), n_total_trials=len(results),
        finished_at=datetime.now(timezone.utc) if finished else None,
        stats=JobStats.from_trial_results(results)).model_dump_json(exclude={'trial_results'}))
    return job


def write_catalog(catalog: Path, generated: list[str], failed: list[str] = ()) -> None:
    """A catalog as convert.py writes it: every case, and the task of each one that generated."""
    for name in generated:
        (catalog / name / 'tests').mkdir(parents=True)
        (catalog / name / 'tests/build.json').write_text(json.dumps({'source_commit': PINNED}))
    (catalog / 'catalog.json').write_text(json.dumps({
        'aacr_revision': 'r', 'dataset_sha256': 's', 'split': 'dev',
        'cases': [{'task_name': name, 'status': 'generated'} for name in generated]
                 + [{'task_name': name, 'status': 'error', 'error': 'unavailable'} for name in failed]}))


@pytest.fixture
def catalog(tmp_path):
    write_catalog(tmp_path / 'catalog', ['case-a', 'case-b'], failed=['case-x'])
    return tmp_path / 'catalog'


def test_each_round_is_aacrs_pooled_metric_then_the_mean_and_sample_spread(tmp_path, catalog):
    pinned = [write_job(tmp_path / 'pinned-1', catalog, {'case-a': (4, 2, 1, 1), 'case-b': (2, 3, 2, 2)}, PINNED),
              write_job(tmp_path / 'pinned-2', catalog, {'case-a': (4, 4, 2, 2), 'case-b': (2, 0, 0, 0)}, PINNED)]
    candidate = [write_job(tmp_path / 'candidate-1', catalog, {'case-a': (4, 6, 1, 1), 'case-b': 'incomplete'},
                           CANDIDATE),
                 write_job(tmp_path / 'candidate-2', catalog, {'case-a': (4, 2, 2, 2), 'case-b': (2, 2, 1, 1)},
                           CANDIDATE)]
    compared = report(catalog, [pinned, candidate])
    assert compared['catalog'] == {'aacr_revision': 'r', 'dataset_sha256': 's', 'split': 'dev'}
    # A case that failed to convert has no task, so it is no case of the catalog's.
    assert {name: block['tasks'] for name, block in compared['blocks'].items()} == {
        'all': ['case-a', 'case-b'], 'matched': ['case-a']}
    pins, cand = compared['sides'][PINNED], compared['sides'][CANDIDATE]
    assert len(compared['sides']) == 2

    # Pooled counts, not an average of per-case rates: round 1 matched 3 of 5 sent and 3 of 6 references.
    one, two = pins['all']['rounds']
    assert (one['expected_notes'], one['generated_notes'], one['matched_generated_notes']) == (6, 5, 3)
    assert [one[key] for key in RATES] == [.6, .5, .545]
    # A complete review that sent no findings is still scored.
    assert [two[key] for key in RATES] == [.5, .333, .4] and two['generated_notes'] == 4
    # The mean and the sample standard deviation (n-1) over rounds.
    summary = pins['all']['summary']
    assert summary['file_semantic_match_rate'] == pytest.approx(
        {'defined_rounds': 2, 'mean': .55, 'stddev': .1 / 2 ** .5})
    assert summary['balanced_score'] == pytest.approx({'defined_rounds': 2, 'mean': .4725, 'stddev': .145 / 2 ** .5})

    # An unscored trial leaves its round's denominators, as in the job's own metric, and is counted.
    unscored = cand['all']['rounds'][0]
    assert (unscored['evaluated_instances'], unscored['missing_instances'], unscored['generated_notes']) == (1, 1, 6)
    assert [unscored[key] for key in RATES] == [.167, .25, .2]
    assert cand['rounds'][0] | {'trials': None, 'harbor': None} == {
        'job': 'candidate-1', 'expected': 2, 'missing': 0, 'produced': 1, 'completed_empty': 0, 'incomplete': 1,
        'failed': 0, 'evaluated': 1, 'conversion_limited': 0, 'omitted_findings': 0,
        'elapsed_seconds': 50, 'cost_usd': .25, 'cost_trials': 1, 'trials': None, 'harbor': None}
    assert pins['rounds'][1]['completed_empty'] == 1 and pins['rounds'][1]['cost_usd'] == .5
    assert cand['rounds'][0]['trials'][0] == {
        'task': 'case-a', 'trial': 'case-a__candidate-1', 'status': 'complete', 'exception': None,
        'reward': reward(4, 6, 1, 1), 'ratios': {'file_semantic_match_rate': .167, 'file_semantic_recall_rate': .25,
                                                 'balanced_score': .2},
        'omitted_findings': 0, 'elapsed_seconds': 30, 'usage': {'cost_usd': .25}, 'side': CANDIDATE}

    # Over the case every job scored, the sides are comparable.
    assert [entry['balanced_score'] for entry in pins['matched']['rounds']] == [.333, .5]
    assert [entry['balanced_score'] for entry in cand['matched']['rounds']] == [.2, .667]
    assert all(entry['total_instances'] == 1 for side in (pins, cand) for entry in side['matched']['rounds'])
    assert cand['matched']['summary']['file_semantic_match_rate'] == pytest.approx(
        {'defined_rounds': 2, 'mean': (.167 + 1) / 2, 'stddev': .833 / 2 ** .5})

    # A round with nothing scored defines no rate, rather than AACR's 0.0, and the matched set is empty.
    down = report(catalog, [[write_job(tmp_path / 'down', catalog, {'case-a': 'incomplete', 'case-b': 'raised'},
                                       PINNED)]])['sides'][PINNED]
    assert [down['all']['rounds'][0][key] for key in RATES] == [None, None, None]
    assert down['all']['summary']['balanced_score'] == {'defined_rounds': 0, 'mean': None, 'stddev': None}
    assert down['matched'] == {} and (down['rounds'][0]['failed'], down['rounds'][0]['incomplete']) == (1, 1)


def test_sides_need_one_build_of_their_own_and_jobs_one_pass_over_the_catalog(tmp_path, catalog):
    scored = {'case-a': (4, 2, 1, 1), 'case-b': (2, 3, 2, 2)}
    first, second = (write_job(tmp_path / f'pinned-{number}', catalog, scored, PINNED) for number in (1, 2))
    other = write_job(tmp_path / 'candidate-1', catalog, scored, CANDIDATE)
    # A side whose jobs mix builds, and two sides that are the same build.
    for sides in ([[first, other]], [[first], [second]]):
        with pytest.raises(ValueError, match='one Daydream build of its own'):
            report(catalog, sides)
    # A job listed twice, however its path is spelled.
    for sides in ([[first, tmp_path / 'candidate-1/../pinned-1']], [[first], [other, first]]):
        with pytest.raises(ValueError, match='listed more than once'):
            report(catalog, sides)
    # Two trials for one case in a job, and a trial for a case outside the catalog, even one that failed to convert.
    shutil.copytree(first / 'case-a__pinned-1', first / 'case-a__again')
    outside = write_job(tmp_path / 'outside', catalog, {'case-x': (1, 1, 1, 1)}, PINNED)
    for job in (first, outside):
        with pytest.raises(ValueError, match='not one pass'):
            report(catalog, [[job]])
