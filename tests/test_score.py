"""Behavioral proof through AACR's real builder, scorer and summary functions."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

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


def daydream_artifacts(tmp_path, findings):
    """A native terminal contract alongside independent filesystem receipts."""
    paths = fixture(tmp_path, findings)
    proof = tmp_path / 'daydream'
    proof.mkdir()
    invocation = str(uuid.uuid4())
    comparison = {'base_sha': 'a' * 40, 'head_sha': 'b' * 40,
                  'merge_base_sha': 'a' * 40, 'diff_key': 'd' * 64,
                  'patch_sha256': 'c' * 64}
    scope = {'scope_id': 'php', 'stack': 'php', 'files': ['src/file.php'],
             'shard': None, 'scope_ref': None}
    terminal = {
        'schema_version': 1, 'run_id': str(uuid.uuid4()),
        'analysis_state': 'complete', 'pipeline_state': 'completed',
        'analyzed_revision': {key: comparison[key] for key in ('head_sha', 'merge_base_sha', 'diff_key')},
        'reason_codes': [], 'planned_scopes': [scope], 'required_phases': ['findings'],
        'stack_outcomes': [dict(scope, status='complete', reason_codes=[], partial_evidence=False)],
        'phase_outcomes': [{'phase': 'findings', 'status': 'complete', 'reason_codes': [],
                            'noop': False, 'usable_evidence': True}],
        'completed_stacks': ['php'], 'failed_stacks': [], 'uncovered_stacks': [],
        'projection_valid': True,
    }
    execution = {
        'schema_version': 1, 'invocation_id': invocation,
        'status': 'complete' if findings else 'completed_empty', 'runner_exit_code': 0,
        'terminal_result': terminal, 'analyzed_comparison': comparison,
        'post_comparison': comparison, 'usage': {}, 'retained_findings': findings,
        'resolved_configuration': {}, 'build': {}, 'diagnostics': [], 'elapsed_seconds': 1,
        'findings_sha256': hashlib.sha256(paths[1].read_bytes()).hexdigest(),
    }
    for name, value in {
        'request.json': {'invocation_id': invocation},
        'host-receipt.json': {'invocation_id': invocation, 'status': 'succeeded', 'return_code': 0},
        'execution.json': execution,
    }.items():
        (proof / name).write_text(json.dumps(value))
    expected = tmp_path / 'comparison.json'
    expected.write_text(json.dumps(comparison))
    host_receipt = tmp_path / 'native-host-receipt.json'
    host_receipt.write_bytes((proof / 'host-receipt.json').read_bytes())
    return paths, proof, expected, host_receipt


@pytest.mark.parametrize('findings,responses', [([], []), ([finding()], ['One agreement\n[1]'])])
def test_daydream_completed_review_requires_native_terminal_and_process_proof(tmp_path, findings, responses):
    paths, proof, expected, host_receipt = daydream_artifacts(tmp_path, findings)
    client = JudgeTransport(*responses)
    result = asyncio.run(score.score_case(*paths, client=client,
                        daydream_proof=proof, comparison_path=expected, host_receipt_path=host_receipt))
    assert result['summary']['generated_notes'] == len(findings)
    assert result['summary']['matched_generated_notes'] == len(findings)
    if findings:
        assert result['eval_res'][0]['generated_comments'][0]['from_line'] is None
    assert (paths[2] / 'reward.json').is_file()


@pytest.mark.parametrize('artifact,field,value', [
    ('host-receipt.json', 'status', 'failed'),
    ('host-receipt.json', 'status', 'cancelled'),
    ('host-receipt.json', 'return_code', 1),
    ('host-receipt.json', 'return_code', False),
    ('host-receipt.json', 'invocation_id', str(uuid.UUID(int=1))),
    ('request.json', 'invocation_id', 'not-an-invocation'),
    ('execution.json', 'invocation_id', str(uuid.UUID(int=1))),
    ('execution.json', 'schema_version', 2),
    ('execution.json', 'runner_exit_code', 1),
    ('execution.json', 'status', 'incomplete'),
    ('execution.json', 'status', 'failed'),
    ('execution.json', 'status', 'cancelled'),
    ('execution.json', 'terminal_result', None),
    ('execution.json', 'findings_sha256', '0' * 64),
])
def test_daydream_partial_stale_or_nonzero_output_cannot_be_empty_success(tmp_path, artifact, field, value):
    paths, proof, expected, host_receipt = daydream_artifacts(tmp_path, [])
    path = proof / artifact
    document = json.loads(path.read_text())
    document[field] = value
    path.write_text(json.dumps(document))
    client = JudgeTransport()
    with pytest.raises(score.VerificationError):
        asyncio.run(score.score_case(*paths, client=client,
                    daydream_proof=proof, comparison_path=expected, host_receipt_path=host_receipt))
    assert not client.requests
    assert not (paths[2] / 'reward.json').exists()
    assert json.loads((paths[2] / 'diagnostic.json').read_text())['status'] == 'failed'


@pytest.mark.parametrize('missing', ['request.json', 'host-receipt.json', 'execution.json', 'comparison.json'])
def test_daydream_missing_proof_fails_before_judging(tmp_path, missing):
    paths, proof, expected, host_receipt = daydream_artifacts(tmp_path, [finding()])
    (expected if missing == 'comparison.json' else proof / missing).unlink()
    client = JudgeTransport('[1]')
    with pytest.raises(score.VerificationError):
        asyncio.run(score.score_case(*paths, client=client,
                    daydream_proof=proof, comparison_path=expected, host_receipt_path=host_receipt))
    assert not client.requests
    assert not (paths[2] / 'reward.json').exists()


@pytest.mark.parametrize('location,key,value', [
    ('expected', 'base_sha', 'mismatched'), ('expected', 'head_sha', 'mismatched'),
    ('expected', 'base_sha', 'e' * 40), ('expected', 'head_sha', 'e' * 40),
    ('analyzed_comparison', 'merge_base_sha', 'mismatched'),
    ('analyzed_comparison', 'patch_sha256', 'mismatched'),
    ('post_comparison', 'head_sha', 'mismatched'), ('post_comparison', 'diff_key', 'mismatched'),
    ('terminal_result', 'head_sha', 'mismatched'), ('terminal_result', 'merge_base_sha', 'mismatched'),
    ('terminal_result', 'diff_key', 'mismatched'),
])
def test_daydream_comparison_mismatch_fails_before_judging(tmp_path, location, key, value):
    paths, proof, expected, host_receipt = daydream_artifacts(tmp_path, [finding()])
    path = expected if location == 'expected' else proof / 'execution.json'
    document = json.loads(path.read_text())
    target = (document if location == 'expected' else document['terminal_result']['analyzed_revision']
              if location == 'terminal_result' else document[location])
    target[key] = value
    path.write_text(json.dumps(document))
    client = JudgeTransport('[1]')
    with pytest.raises(score.VerificationError) as error:
        asyncio.run(score.score_case(*paths, client=client,
                    daydream_proof=proof, comparison_path=expected, host_receipt_path=host_receipt))
    if location == 'expected':
        assert str(error.value.__cause__) == 'Daydream expected comparison does not match the review case'
    assert not client.requests
    assert not (paths[2] / 'reward.json').exists()


def test_daydream_contradictory_complete_terminal_is_rejected_by_native_validator(tmp_path):
    paths, proof, expected, host_receipt = daydream_artifacts(tmp_path, [])
    path = proof / 'execution.json'
    document = json.loads(path.read_text())
    document['terminal_result']['stack_outcomes'][0]['status'] = 'uncovered'
    document['terminal_result']['stack_outcomes'][0]['reason_codes'] = ['coverage_unknown']
    path.write_text(json.dumps(document))
    with pytest.raises(score.VerificationError) as error:
        asyncio.run(score.score_case(*paths, client=JudgeTransport(),
                    daydream_proof=proof, comparison_path=expected, host_receipt_path=host_receipt))
    assert isinstance(error.value.__cause__, ValueError)
    assert 'completed_stacks disagrees' in str(error.value.__cause__)
    assert not (paths[2] / 'reward.json').exists()


@pytest.mark.parametrize('pipeline', ['completed', 'cancelled'])
def test_daydream_valid_unfinished_terminal_cannot_publish_a_callback_output(tmp_path, pipeline):
    from daydream.review_result import validate_terminal_result

    paths, proof, expected, host_receipt = daydream_artifacts(tmp_path, [])
    path = proof / 'execution.json'
    document = json.loads(path.read_text())
    terminal = document['terminal_result']
    terminal['pipeline_state'] = pipeline
    terminal['analysis_state'] = 'incomplete'
    terminal['phase_outcomes'][0].update(status='incomplete', reason_codes=['backend_failure'])
    terminal['reason_codes'] = ['backend_failure'] + (['interruption'] if pipeline == 'cancelled' else [])
    # This is valid native partial coverage, not merely a malformed artifact.
    validate_terminal_result(terminal)
    path.write_text(json.dumps(document))
    client = JudgeTransport()
    with pytest.raises(score.VerificationError):
        asyncio.run(score.score_case(*paths, client=client,
                    daydream_proof=proof, comparison_path=expected, host_receipt_path=host_receipt))
    assert not client.requests
    assert not (paths[2] / 'reward.json').exists()


@pytest.mark.parametrize('missing', ['daydream_proof', 'comparison_path', 'host_receipt_path'])
def test_daydream_completion_options_cannot_silently_select_oracle_boundary(tmp_path, missing):
    paths, proof, expected, host_receipt = daydream_artifacts(tmp_path, [])
    options = {'daydream_proof': proof, 'comparison_path': expected, 'host_receipt_path': host_receipt}
    del options[missing]
    with pytest.raises(score.VerificationError):
        asyncio.run(score.score_case(*paths, client=JudgeTransport(), **options))
    assert not (paths[2] / 'reward.json').exists()


def test_failed_reverification_removes_prior_rewards(tmp_path):
    paths = fixture(tmp_path, [])
    run(paths, JudgeTransport())
    (paths[2] / 'reward.txt').write_text('1')
    paths[1].unlink()
    with pytest.raises(score.VerificationError):
        run(paths, JudgeTransport())
    assert not (paths[2] / 'reward.json').exists()
    assert not (paths[2] / 'reward.txt').exists()


def test_daydream_requires_separate_native_host_receipt(tmp_path):
    paths, proof, expected, host_receipt = daydream_artifacts(tmp_path, [])
    with pytest.raises(score.VerificationError):
        asyncio.run(score.score_case(*paths, client=JudgeTransport(),
                    daydream_proof=proof, comparison_path=expected,
                    host_receipt_path=proof / 'host-receipt.json'))
    assert not (paths[2] / 'reward.json').exists()


@pytest.mark.parametrize('field,value', [
    ('status', 'cancelled'), ('status', 'failed'),
    ('return_code', 1), ('return_code', False),
    ('invocation_id', str(uuid.UUID(int=1))),
])
def test_daydream_native_host_receipt_overrides_sandbox_success(tmp_path, field, value):
    paths, proof, expected, host_receipt = daydream_artifacts(tmp_path, [])
    document = json.loads(host_receipt.read_text())
    document[field] = value
    host_receipt.write_text(json.dumps(document))
    # Sandbox upload may have finished before the host observed cancellation.
    assert json.loads((proof / 'host-receipt.json').read_text())['status'] == 'succeeded'
    client = JudgeTransport()
    with pytest.raises(score.VerificationError):
        asyncio.run(score.score_case(*paths, client=client, daydream_proof=proof,
                    comparison_path=expected, host_receipt_path=host_receipt))
    assert not client.requests
    assert not (paths[2] / 'reward.json').exists()


def test_daydream_missing_native_host_artifact_cannot_publish_reward(tmp_path):
    paths, proof, expected, host_receipt = daydream_artifacts(tmp_path, [])
    host_receipt.unlink()
    with pytest.raises(score.VerificationError):
        asyncio.run(score.score_case(*paths, client=JudgeTransport(), daydream_proof=proof,
                    comparison_path=expected, host_receipt_path=host_receipt))
    assert not (paths[2] / 'reward.json').exists()
