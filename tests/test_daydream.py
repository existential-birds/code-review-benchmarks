"""Exercise the sandbox reviewer boundary with real Git and native terminal evidence."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
from types import SimpleNamespace
import uuid

import pytest

from benchmarks.aacr import daydream_entrypoint as entrypoint


@pytest.fixture
def review_case(tmp_path, monkeypatch):
    from daydream import git_ops

    repo = tmp_path / 'repo'
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
    git('init', '-q')
    (repo / 'file.php').write_text('base\n')
    git('add', '.')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'base')
    base = git('rev-parse', 'HEAD')
    (repo / 'file.php').write_text('head\n')
    git('add', '.')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'head')
    head = git('rev-parse', 'HEAD')
    patch = git_ops.diff(repo, base)
    comparison = dict(base_sha=base, head_sha=head, merge_base_sha=base,
                      diff_key=hashlib.sha256(patch.encode()).hexdigest(),
                      patch_sha256=hashlib.sha256(patch.encode()).hexdigest())
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    (inputs / 'comparison.json').write_text(json.dumps(comparison))
    (inputs / 'diff.patch').write_text(patch)
    (inputs / 'build.json').write_text(json.dumps({'source_commit': 'immutable-test-build'}))
    artifacts = tmp_path / 'artifacts'
    proof = artifacts / 'daydream'
    proof.mkdir(parents=True)
    invocation = str(uuid.uuid4())
    (proof / 'request.json').write_text(json.dumps({'invocation_id': invocation}))
    logs = tmp_path / 'agent'
    logs.mkdir()
    credential_home = inputs / 'credential-store'
    credential_home.mkdir(mode=0o700)
    claims = base64.urlsafe_b64encode(json.dumps({'exp': 4000000000}).encode()).decode().rstrip('=')
    auth = credential_home / 'auth.json'
    auth.write_text(json.dumps({'auth_mode': 'chatgpt', 'tokens': {
        'access_token': 'synthetic.' + claims + '.signature',
        'refresh_token': 'synthetic-refresh', 'account_id': 'synthetic-account',
        'id_token': 'synthetic-id'}}))
    auth.chmod(0o600)
    return SimpleNamespace(repo=repo, inputs=inputs, artifacts=artifacts, logs=logs,
                           invocation=invocation, comparison=comparison, git=git)


def finalized(case, items, *, complete=True):
    from daydream.review_result import AnalyzedRevision, FinalizedReview, ReviewCoverage
    coverage = ReviewCoverage(str(uuid.uuid4()), AnalyzedRevision(case.comparison['head_sha'],
        case.comparison['merge_base_sha'], case.comparison['diff_key']), [], ['no_diff'])
    coverage.record_phase('no_diff', 'complete', noop=True)
    terminal = coverage.finalize(pipeline_state='completed', projection_valid=True)
    if not complete:
        terminal['pipeline_state'] = 'cancelled'
        terminal['analysis_state'] = 'incomplete'
        terminal['reason_codes'] = ['interruption']
    return FinalizedReview(terminal, items,
        {'prompt_tokens': 120, 'cached_tokens': 70, 'completion_tokens': 11})


def run_case(case):
    return asyncio.run(entrypoint.review_once(repository=case.repo, inputs=case.inputs,
        artifacts=case.artifacts, agent_logs=case.logs, invocation_id=case.invocation,
        credential_home=case.inputs / 'credential-store'))


@pytest.mark.parametrize('items', [[], [{'file': 'file.php', 'line': None,
    'description': 'A whole-file concern', 'rationale': 'Why it matters'}]])
def test_completed_review_exports_only_finalized_items_and_isolated_defaults(review_case, monkeypatch, items):
    from daydream import runner
    case = review_case
    monkeypatch.setenv('DAYDREAM_REVIEW_PROFILE', '/missing/operator-profile')
    monkeypatch.setenv('DAYDREAM_FANOUT_CONCURRENCY', '1')
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-reach-native-transport')
    calls = []
    async def inference(config, *, review_result_handler, execution):
        calls.append(config)
        assert config.backend == 'codex' and config.flow_name == 'review'
        assert config.output_mode == 'review' and config.precision_mode is False
        assert config.model is None and config.reasoning_effort is None
        assert config.file_config.phases == {}
        assert config.review_profile.source_kind == 'default'
        assert config.dataset_capture_disabled and not config.archive and not config.run_eval
        assert not config.discover_github_provenance
        assert execution.backend.fanout_concurrency == 8
        assert 'OPENAI_API_KEY' not in execution.backend.child_environment()
        role_home = Path(execution.backend.child_environment()['CODEX_HOME'])
        assert (role_home / 'auth.json').is_symlink()
        assert (role_home / 'auth.json').resolve() == case.inputs / 'credential-store/auth.json'
        assert 'cli_auth_credentials_store = "file"' in (role_home / 'config.toml').read_text()
        stage = Path(config.dump_artifacts)
        stage.mkdir(exist_ok=True)
        (stage / 'trajectory.json').write_text(json.dumps({'session_id': str(uuid.uuid4())}))
        review_result_handler(finalized(case, items))
        return 0
    monkeypatch.setattr(runner, 'run', inference)
    assert run_case(case) == 0
    assert len(calls) == 1
    findings = json.loads((case.artifacts / 'findings.json').read_text())['review_output']
    assert len(findings) == len(items)
    if items:
        assert findings[0]['file'] == 'file.php'
        assert findings[0]['start_line'] is None and findings[0]['end_line'] is None
        assert 'Why it matters' in findings[0]['description']
    proof = json.loads((case.artifacts / 'daydream/execution.json').read_text())
    assert proof['terminal_result']['analysis_state'] == 'complete'
    assert proof['analyzed_comparison'] == proof['post_comparison'] == case.comparison
    assert proof['usage']['prompt_tokens'] == 120
    assert proof['cost_interpretation'] == 'API-equivalent estimate when available; subscription charges unknown'
    assert proof['retained_findings'] == items
    assert proof['findings_sha256'] == hashlib.sha256((case.artifacts / 'findings.json').read_bytes()).hexdigest()
    assert (case.logs / 'trajectory.json').exists()


def test_configuration_reads_native_routing_after_runner_copies_config(review_case, monkeypatch):
    from dataclasses import replace
    from daydream import runner
    from daydream.deep.latency import RiskSummary, route_for
    from daydream.deep.routing_record import write_routing_record
    from daydream.run_config import _resolved_reasoning_effort
    case = review_case
    routing = {
        'profile': {'selected': 'balanced', 'source': 'default'},
        'risk': {'floors': ['migration_surface']},
        'wonder': {'outcome': 'run', 'effort': 'high', 'reason': 'native risk floor'},
        'arbiter': {'sharded': True, 'groups': [
            {'group_id': 'group-0', 'effort': 'high', 'target_uids': ['a'], 'reused': False},
            {'group_id': 'group-1', 'effort': 'xhigh', 'target_uids': ['b'], 'reused': False}]},
    }
    async def inference(config, *, review_result_handler, execution):
        copied = replace(config, dataset_capture=False)
        copied.latency_route = route_for('balanced', RiskSummary(0, 0, ('migration_surface',)))
        assert config.latency_route is None
        assert _resolved_reasoning_effort(copied, 'wonder') == 'high'
        stage = Path(copied.dump_artifacts)
        (stage / 'deep').mkdir()
        write_routing_record(stage / 'deep', routing)
        (stage / 'trajectory.json').write_text(json.dumps({'session_id': str(uuid.uuid4())}))
        review_result_handler(finalized(case, []))
        return 0
    monkeypatch.setattr(runner, 'run', inference)
    assert run_case(case) == 0
    settings = json.loads((case.artifacts / 'daydream/execution.json').read_text())['resolved_configuration']
    assert settings['latency_routing'] == routing
    assert settings['latency_routing']['wonder']['effort'] == 'high'
    assert [group['effort'] for group in settings['latency_routing']['arbiter']['groups']] == ['high', 'xhigh']
    assert settings['phase_defaults']['wonder']['reasoning_effort'] == 'medium'
    assert 'latency_route' not in settings and 'phase_settings' not in settings


@pytest.mark.parametrize('invalid', ['api_key', 'missing_refresh', 'invalid_jwt'])
def test_native_credential_admission_blocks_inference(review_case, monkeypatch, invalid):
    from daydream import runner
    case = review_case
    auth_path = case.inputs / 'credential-store/auth.json'
    auth = json.loads(auth_path.read_text())
    if invalid == 'api_key':
        auth['OPENAI_API_KEY'] = 'synthetic-api-key'
    elif invalid == 'missing_refresh':
        del auth['tokens']['refresh_token']
    else:
        auth['tokens']['access_token'] = 'malformed'
    auth_path.write_text(json.dumps(auth))
    async def unexpected(*args, **kwargs):
        pytest.fail('native auth admission must reject before inference')
    monkeypatch.setattr(runner, 'run', unexpected)
    assert run_case(case) != 0
    proof = json.loads((case.artifacts / 'daydream/execution.json').read_text())
    assert proof['status'] == 'failed'
    assert any('SubscriptionAuthError' in message for message in proof['diagnostics'])
    assert not (case.artifacts / 'findings.json').exists()


@pytest.mark.parametrize('failure', ['missing', 'nonzero', 'incomplete', 'exception', 'cancelled', 'changed', 'created'])
def test_unsuccessful_invocation_retains_evidence_without_findings(review_case, monkeypatch, failure):
    from daydream import runner
    case = review_case
    async def inference(config, *, review_result_handler, execution):
        stage = Path(config.dump_artifacts)
        stage.mkdir(exist_ok=True)
        (stage / 'trajectory.json').write_text(json.dumps({'session_id': str(uuid.uuid4())}))
        if failure != 'missing':
            review_result_handler(finalized(case, [], complete=failure != 'incomplete'))
        if failure == 'exception':
            raise RuntimeError('provider failed after callback')
        if failure == 'cancelled':
            raise asyncio.CancelledError
        if failure == 'changed':
            (case.repo / 'file.php').write_text('changed during review\n')
        if failure == 'created':
            (case.repo / 'added.php').write_text('created during review\n')
        return 1 if failure == 'nonzero' else 0
    monkeypatch.setattr(runner, 'run', inference)
    assert run_case(case) != 0
    assert not (case.artifacts / 'findings.json').exists()
    proof = json.loads((case.artifacts / 'daydream/execution.json').read_text())
    assert proof['status'] not in {'complete', 'completed_empty'}
    assert proof['diagnostics']
    assert proof['usage'].get('prompt_tokens') == (None if failure == 'missing' else 120)


@pytest.mark.parametrize('outcome', [0, 7, 'cancelled', 'cancelled_download', 'cancelled_receipt'])
def test_installed_agent_requires_process_success_and_populates_usage(tmp_path, outcome):
    from benchmarks.aacr.daydream_agent import DaydreamAgent
    from harbor.models.agent.context import AgentContext

    class Environment:
        def __init__(self):
            self.uploads = {}
            self.invocations = []
            self.interrupted = False
        def is_path_mounted(self, environment_path, host_path):
            return environment_path == '/logs/agent' and host_path == logs
        async def upload_file(self, source_path, target_path):
            value = json.loads(Path(source_path).read_text())
            self.uploads[target_path] = value
            if outcome == 'cancelled_receipt' and value.get('status') == 'succeeded' and not self.interrupted:
                self.interrupted = True
                raise asyncio.CancelledError
        async def download_file(self, source_path, target_path):
            if outcome == 'cancelled_download' and not self.interrupted:
                self.interrupted = True
                raise asyncio.CancelledError
            Path(target_path).write_text(json.dumps({'usage': {'prompt_tokens': 120,
                'cached_tokens': 70, 'completion_tokens': 11}, 'elapsed_seconds': 5}))
        async def exec(self, command, **kwargs):
            if '-m benchmarks.aacr.daydream_entrypoint' in command:
                self.invocations.append(command)
                if outcome == 'cancelled':
                    raise asyncio.CancelledError
                return SimpleNamespace(return_code=outcome if type(outcome) is int else 0, stdout='', stderr='')
            return SimpleNamespace(return_code=0, stdout='', stderr='')

    environment = Environment()
    logs = tmp_path / 'agent'
    logs.mkdir()
    agent = DaydreamAgent(logs_dir=logs)
    context = AgentContext()
    async def run():
        await agent.run('review the pinned comparison', environment, context)
    if isinstance(outcome, str):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run())
    elif outcome:
        with pytest.raises(RuntimeError):
            asyncio.run(run())
    else:
        asyncio.run(run())
    assert len(environment.invocations) == 1
    request = environment.uploads['/logs/artifacts/daydream/request.json']
    receipt = environment.uploads['/logs/artifacts/daydream/host-receipt.json']
    assert receipt['invocation_id'] == request['invocation_id']
    assert receipt['status'] == ('succeeded' if outcome == 0 else 'cancelled' if isinstance(outcome, str) else 'failed')
    assert context.n_input_tokens == 120 and context.n_cache_tokens == 70
    assert context.n_output_tokens == 11
    assert context.cost_usd is None
    assert context.metadata['elapsed_seconds'] == 5
    # A residual sandbox process can rewrite its bind-mounted agent logs after
    # the host observes cancellation; this must not replace host process state.
    (logs / 'host-receipt.json').write_text(json.dumps(dict(receipt, status='succeeded', return_code=0)))
    authoritative = json.loads((logs.parent / 'daydream-receipt.json').read_text())
    assert authoritative == receipt


@pytest.mark.parametrize('phase', ['install', 'run'])
def test_copied_agent_logs_are_rejected_before_any_runtime_execution(tmp_path, phase):
    from benchmarks.aacr.daydream_agent import DaydreamAgent
    from harbor.models.agent.context import AgentContext
    logs = tmp_path / 'agent'
    logs.mkdir()
    class Environment:
        def is_path_mounted(self, environment_path, host_path):
            assert environment_path == '/logs/agent' and host_path == logs
            return False
        async def exec(self, *args, **kwargs):
            pytest.fail('copied logs must fail admission before any reviewer setup or invocation')
    agent = DaydreamAgent(logs_dir=logs)
    async def run():
        if phase == 'install':
            await agent.install(Environment())
        else:
            await agent.run('review the pinned comparison', Environment(), AgentContext())
    with pytest.raises(RuntimeError, match='shared.*agent.*log'):
        asyncio.run(run())
    assert not (logs / 'request.json').exists()


def test_native_namespace_failure_blocks_runtime_admission(tmp_path):
    from benchmarks.aacr.daydream_agent import DaydreamAgent
    commands = []
    logs = tmp_path / 'agent'
    logs.mkdir()
    class Environment:
        def is_path_mounted(self, environment_path, host_path):
            return environment_path == '/logs/agent' and host_path == logs
        async def exec(self, command, **kwargs):
            commands.append(command)
            return SimpleNamespace(return_code=1 if command == 'unshare -Ur true' else 0)
    with pytest.raises(RuntimeError, match='namespace'):
        asyncio.run(DaydreamAgent(logs_dir=logs).install(Environment()))
    assert commands == ['unshare -Ur true']


@pytest.mark.parametrize('mismatch', ['stale', 'head', 'patch', 'request'])
def test_stale_or_mismatched_inputs_never_invoke_reviewer(review_case, monkeypatch, mismatch):
    from daydream import runner
    case = review_case
    if mismatch == 'stale':
        (case.artifacts / 'findings.json').write_text('{"review_output": []}')
    elif mismatch == 'head':
        case.git('checkout', '-q', case.comparison['base_sha'])
    elif mismatch == 'patch':
        (case.inputs / 'diff.patch').write_text('a different patch')
    else:
        (case.artifacts / 'daydream/request.json').write_text(json.dumps({'invocation_id': str(uuid.uuid4())}))
    async def unexpected(*args, **kwargs):
        pytest.fail('reviewer must not run for an invalid invocation')
    monkeypatch.setattr(runner, 'run', unexpected)
    assert run_case(case) != 0


def test_sigterm_joins_native_finalization_and_flushes_partial_recorders(monkeypatch):
    import anyio
    from daydream import trajectory
    observed = []
    async def review(**kwargs):
        try:
            await anyio.sleep_forever()
        finally:
            observed.append('finalized')
    monkeypatch.setattr(entrypoint, 'review_once', review)
    monkeypatch.setattr(trajectory, 'flush_active_signal_recorders', lambda: observed.append('flushed'))
    async def run():
        asyncio.get_running_loop().call_soon(os.kill, os.getpid(), signal.SIGTERM)
        return await entrypoint._main(str(uuid.uuid4()))
    assert asyncio.run(run()) == 130
    assert observed == ['flushed', 'finalized']
