"""Behavioral proof through AACR's real builder, scorer and summary functions."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.aacr import score


class JudgeTransport:
    """Only the completion transport is replaced; matching remains AACR code."""
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []
        self.failures = []
        self.chat = self.completions = self

    async def create(self, **request):
        self.requests.append(request)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=response))])


def fixture(tmp_path, findings, references=2):
    instance = tmp_path / 'instance.jsonl'
    instance.write_text(json.dumps({
        'instance_id': 'owner__repo@abcdef0', 'repo': 'owner/repo',
        'base_commit': 'a' * 40, 'head_commit': 'b' * 40,
        'reference_comments': [
            {'path': 'src/file.php', 'text': f'Concern {i}', 'start_line': None, 'end_line': None}
            for i in range(references)
        ],
    }) + '\n')
    path = tmp_path / 'findings.json'
    path.write_text(json.dumps({'review_output': findings}))
    return instance, path, tmp_path / 'evidence'


def finding(file='src/file.php'):
    return {'file': file, 'start_line': None, 'end_line': None,
            'summary': 'A concern', 'description': 'Details of the failure'}


def run(paths, client):
    return asyncio.run(score.score_case(*paths, client=client))


def test_duplicate_hit_and_one_to_many_keep_both_distinct_counts(tmp_path):
    paths = fixture(tmp_path, [finding(), finding(), finding('other.php')], references=3)
    client = JudgeTransport('Two covered concerns\n[1, 2]', 'Duplicate coverage\n[1, 2]')
    result = run(paths, client)
    summary = result['summary']
    assert summary['expected_notes'] == 3
    assert summary['generated_notes'] == 3
    assert summary['matched_file_semantic_notes'] == 2
    assert summary['matched_generated_notes'] == 1
    assert summary['file_semantic_match_rate'] == .333
    assert summary['file_semantic_recall_rate'] == .667
    assert summary['balanced_score'] == .444
    records = result['eval_res'][0]['generated_comments']
    assert [r['hit_new_gt'] for r in records] == [True, False, False]
    assert records[0]['from_line'] is None and records[0]['to_line'] is None
    assert len(client.requests) == 2  # no judge for a different file
    assert '[L?]' in client.requests[0]['messages'][0]['content']
    assert json.loads((paths[2] / 'reward.json').read_text())['balanced_score'] == .444


def test_normalized_files_and_final_recognized_verdict_use_upstream_parser(tmp_path):
    paths = fixture(tmp_path, [finding('src\\file.php')])
    client = JudgeTransport('Draft [1], final coverage:\n[1, 2]')
    result = run(paths, client)
    assert result['summary']['matched_file_semantic_notes'] == 2
    assert result['summary']['matched_generated_notes'] == 1
    assert result['summary']['balanced_score'] == 1


@pytest.mark.parametrize('findings,responses', [([], []), ([finding()], ['No shared concern\n[]'])])
def test_completed_empty_review_and_valid_nonmatch_are_scored(tmp_path, findings, responses):
    paths = fixture(tmp_path, findings)
    result = run(paths, JudgeTransport(*responses))
    assert result['summary']['generated_notes'] == len(findings)
    assert result['summary']['expected_notes'] == 2
    assert result['summary']['balanced_score'] == 0
    assert (paths[2] / 'reward.json').is_file()


@pytest.mark.parametrize('response', [
    'No verdict here', 'Invalid array [1,]', '', None,
    RuntimeError('authentication failed'), OSError('transport broke'),
])
def test_swallowed_judge_failures_leave_diagnostics_without_reward(tmp_path, response):
    paths = fixture(tmp_path, [finding()])
    with pytest.raises(score.VerificationError):
        run(paths, JudgeTransport(response))
    diagnostic = json.loads((paths[2] / 'diagnostic.json').read_text())
    assert diagnostic['status'] == 'failed'
    assert diagnostic['failures']
    assert not (paths[2] / 'reward.json').exists()
    assert not (paths[2] / 'reward.txt').exists()
    # Upstream still emitted a result after swallowing; the wrapper rejects it.
    assert list((paths[2] / 'upstream').glob('metrics_*.json'))


@pytest.mark.parametrize('payload', [None, [], {}, {'review_output': None},
    {'review_output': [{}]}, {'review_output': [dict(finding(), start_line=True)]},
    {'review_output': [dict(finding(), file='')]}, {'review_output': [dict(finding(), description=4)]}])
def test_missing_or_invalid_findings_fail_before_scoring(tmp_path, payload):
    paths = fixture(tmp_path, [])
    paths[1].unlink()
    if payload is not None:
        paths[1].write_text(json.dumps(payload))
    client = JudgeTransport()
    with pytest.raises(score.VerificationError):
        run(paths, client)
    assert not client.requests
    assert json.loads((paths[2] / 'diagnostic.json').read_text())['status'] == 'failed'
    assert not (paths[2] / 'reward.json').exists()


def test_judge_full_response_is_retained_beyond_upstream_truncation(tmp_path):
    paths = fixture(tmp_path, [finding()])
    response = 'Reason ' * 120 + '\n[1, 2]'
    result = run(paths, JudgeTransport(response))
    assert len(result['eval_res'][0]['generated_comments'][0]['judge_reason']) == 500
    evidence = json.loads((paths[2] / 'verdicts.json').read_text())
    assert evidence[0]['response'] == response


@pytest.mark.parametrize('response', ['Matched\n[1]', RuntimeError('judge call failed')])
def test_verdict_evidence_write_failure_withholds_reward(tmp_path, monkeypatch, response):
    paths = fixture(tmp_path, [finding()], references=1)
    write_text = Path.write_text

    def fail_verdict_write(path, *args, **kwargs):
        if path == paths[2] / 'verdicts.json.tmp':
            raise PermissionError('Cannot write verdict evidence')
        return write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', fail_verdict_write)
    with pytest.raises(score.VerificationError):
        run(paths, JudgeTransport(response))
    diagnostic = json.loads((paths[2] / 'diagnostic.json').read_text())
    assert diagnostic['status'] == 'failed'
    assert {'category': 'evidence_write', 'error_type': 'PermissionError'} in diagnostic['failures']
    if isinstance(response, Exception):
        assert {'category': 'judge_call', 'error_type': 'RuntimeError'} in diagnostic['failures']
    assert not (paths[2] / 'reward.json').exists()
    assert not (paths[2] / 'reward.txt').exists()


def test_upstream_missing_case_cannot_publish_reward(tmp_path, monkeypatch):
    paths = fixture(tmp_path, [])
    from benchmarks.aacr.upstream import evaluate
    monkeypatch.setattr(evaluate, 'load_target_comments', lambda *args: None)
    with pytest.raises(score.VerificationError):
        run(paths, JudgeTransport())
    assert not (paths[2] / 'reward.json').exists()


def test_transport_sticky_failure_is_checked_even_with_valid_verdict(tmp_path):
    paths = fixture(tmp_path, [finding()])
    client = JudgeTransport('[1]')
    client.failures.append({'category': 'transport'})
    with pytest.raises(score.VerificationError):
        run(paths, client)
    assert not (paths[2] / 'reward.json').exists()
