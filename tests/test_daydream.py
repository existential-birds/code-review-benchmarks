"""The sandbox reviewer, with a faked Git checkout and Daydream's terminal results."""
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

from benchmarks.daydream import entrypoint
from benchmarks.daydream.build import BUILD
from fake_git import BASE, HEAD, FakeCheckout


@pytest.fixture
def review_case(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'file.php').write_text('head\n')
    checkout = FakeCheckout(repo).install(monkeypatch)
    patch = checkout.patch
    comparison = dict(base_sha=BASE, head_sha=HEAD, merge_base_sha=BASE,
                      diff_key=hashlib.sha256(patch.encode()).hexdigest(),
                      patch_sha256=hashlib.sha256(patch.encode()).hexdigest())
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    artifacts = tmp_path / 'artifacts'
    (inputs / 'case.json').write_text(json.dumps({'format': 'aacr', 'repository': str(repo),
                                                  'findings': str(artifacts / 'findings.json')}))
    (inputs / 'comparison.json').write_text(json.dumps(comparison))
    (inputs / 'diff.patch').write_text(patch)
    (inputs / 'build.json').write_text(json.dumps({'source_commit': 'immutable-test-build'}))
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
                           invocation=invocation, comparison=comparison, checkout=checkout)


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


def run_case(case, **kwargs):
    return asyncio.run(entrypoint.review_once(case_dir=case.inputs, artifacts=case.artifacts, agent_logs=case.logs, invocation_id=case.invocation,
        credential_home=case.inputs / 'credential-store', **kwargs))


@pytest.mark.parametrize('items', [[], [{'file': 'file.php', 'line': None,
    'description': 'A whole-file concern', 'rationale': 'Why it matters'}]])
@pytest.mark.parametrize('capture_data', [False, True])
def test_completed_review_exports_only_finalized_items_and_isolated_defaults(review_case, monkeypatch, items, capture_data):
    from daydream import runner
    case = review_case
    monkeypatch.setenv('DAYDREAM_REVIEW_PROFILE', '/missing/operator-profile')
    monkeypatch.setenv('DAYDREAM_FANOUT_CONCURRENCY', '1')
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-reach-codex')
    monkeypatch.setenv('DAYDREAM_TRAJECTORY_HUB_REPO', 'must-not-publish/benchmark-data')
    calls = []
    async def inference(config, *, review_result_handler, execution):
        calls.append(config)
        assert config.backend == 'codex' and config.flow_name == 'review'
        assert config.output_mode == 'review' and config.precision_mode is False
        assert config.model is None and config.reasoning_effort is None
        assert config.file_config.phases == {}
        assert config.review_profile.source_kind == 'default'
        assert config.dataset_capture is capture_data
        assert config.dataset_capture_disabled is not capture_data
        assert config.dataset_store_path == (case.logs / 'dataset' if capture_data else None)
        assert config.trajectory_hub_repo is None
        assert 'DAYDREAM_TRAJECTORY_HUB_REPO' not in os.environ
        assert not config.archive and not config.run_eval
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
    assert run_case(case, capture_data=capture_data) == 0
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
    assert proof['resolved_configuration']['dataset_capture'] is capture_data
    assert proof['resolved_configuration']['dataset_store_path'] == (str(case.logs / 'dataset') if capture_data else None)
    assert proof['findings_sha256'] == hashlib.sha256((case.artifacts / 'findings.json').read_bytes()).hexdigest()
    assert (case.logs / 'trajectory.json').exists()


def test_configuration_reads_routing_from_run_record_after_runner_copies_config(review_case, monkeypatch):
    from dataclasses import replace
    from daydream import runner
    from daydream.deep.latency import RiskSummary, route_for
    from daydream.deep.routing_record import write_routing_record
    from daydream.run_config import _resolved_reasoning_effort
    case = review_case
    routing = {
        'profile': {'selected': 'balanced', 'source': 'default'},
        'risk': {'floors': ['migration_surface']},
        'wonder': {'outcome': 'run', 'effort': 'high', 'reason': 'risk floor'},
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
def test_invalid_codex_login_stops_review_before_inference(review_case, monkeypatch, invalid):
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
        pytest.fail('an invalid login must stop the review before inference')
    monkeypatch.setattr(runner, 'run', unexpected)
    assert run_case(case) != 0
    proof = json.loads((case.artifacts / 'daydream/execution.json').read_text())
    assert proof['status'] == 'failed'
    assert any('subscription login' in message for message in proof['diagnostics'])
    assert not (case.artifacts / 'findings.json').exists()


@pytest.mark.parametrize('failure', ['missing', 'nonzero', 'incomplete', 'exception', 'cancelled', 'changed', 'created',
                                     'no-policy-in-build', 'committed-capture', 'committed-refused',
                                     'committed-other-policy', 'committed-other-profile', 'committed-uninstalled'])
def test_unsuccessful_invocation_retains_evidence_without_findings(review_case, monkeypatch, failure):
    from dataclasses import dataclass
    from daydream import committed_review, review_profile, runner
    from daydream.committed_review_model import CommittedReviewResult
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
    # A requested committed review that cannot run as asked fails the trial; the
    # default review above stays installed and must never stand in for it.
    committed = failure.startswith(('no-policy', 'committed'))
    @dataclass(frozen=True)
    class Pipeline:
        assignment_policy: str = 'broad'
    @dataclass(frozen=True)
    class PinnedPipeline:
        """The pinned 0.34.0 build's pipeline has no assignment policy."""
    @dataclass(frozen=True)
    class Profile:
        pipeline: object = PinnedPipeline() if failure == 'no-policy-in-build' else Pipeline()
        digest = 'requested digest'
    reviews = []
    async def review(request):
        reviews.append(request)
        if failure == 'committed-refused':
            raise ValueError('committed review discovery requires a backend with native host tools')
        terminal = finalized(case, []).terminal_result
        run = case.repo / '.daydream/runs' / terminal['run_id']
        run.mkdir(parents=True)
        (run / 'trajectory.json').write_text(json.dumps({'session_id': terminal['run_id']}))
        # A complete review, but sealed under another policy or profile, or never installed.
        return CommittedReviewResult(json.dumps({'run_id': terminal['run_id'], 'terminal_result': terminal,
            'findings': [], 'settings': {
                'assignment_policy': {'name': 'broad' if failure == 'committed-other-policy' else 'adaptive'},
                'profile_digest': 'another digest' if failure == 'committed-other-profile' else 'requested digest'}}),
            failure != 'committed-uninstalled', 'complete')
    if committed:
        monkeypatch.setattr(review_profile, 'build_default_profile', Profile)
        monkeypatch.setattr(committed_review, 'run_committed_review', review)
    assert run_case(case, **({'assignment_policy': 'adaptive', 'capture_data': failure == 'committed-capture'}
                             if committed else {})) != 0
    assert not (case.artifacts / 'findings.json').exists()
    proof = json.loads((case.artifacts / 'daydream/execution.json').read_text())
    assert proof['status'] not in {'complete', 'completed_empty'}
    assert proof['diagnostics']
    assert proof['usage'].get('prompt_tokens') == (None if failure == 'missing' or committed else 120)
    if committed:
        # Neither a build without the policy nor a capture request reaches a model.
        reviewed, exit_code, diagnostic = {
            'no-policy-in-build': (0, None, "got an unexpected keyword argument 'assignment_policy'"),
            'committed-capture': (0, None, 'capture_data=false'),
            'committed-refused': (1, None, 'Daydream raised ValueError'),
            'committed-other-policy': (1, 0, 'another policy or profile'),
            'committed-other-profile': (1, 0, 'another policy or profile'),
            'committed-uninstalled': (1, 1, 'Daydream did not exit successfully')}[failure]
        assert (len(reviews), proof['runner_exit_code'], proof['assignment_policy']) == (reviewed, exit_code, 'adaptive')
        assert diagnostic in ' '.join(proof['diagnostics'])


@pytest.mark.parametrize('outcome,capture_data,assignment_policy', [(0, False, None), (0, True, None),
    (0, False, 'adaptive'), (7, False, None), ('cancelled', False, None), ('cancelled_download', False, None),
    ('cancelled_receipt', False, None)])
def test_installed_agent_requires_process_success_and_populates_usage(tmp_path, outcome, capture_data, assignment_policy):
    from benchmarks.daydream.agent import DaydreamAgent
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
            if '-m benchmarks.daydream.entrypoint' in command:
                self.invocations.append(command)
                if outcome == 'cancelled':
                    raise asyncio.CancelledError
                return SimpleNamespace(return_code=outcome if type(outcome) is int else 0, stdout='', stderr='')
            return SimpleNamespace(return_code=0, stdout='', stderr='')

    environment = Environment()
    logs = tmp_path / 'agent'
    logs.mkdir()
    agent = DaydreamAgent(logs_dir=logs, capture_data=capture_data, assignment_policy=assignment_policy)
    # Harbor refuses a policy the option does not name, before any trial starts.
    with pytest.raises(ValueError, match="assignment_policy.*'broad' or 'adaptive'"):
        DaydreamAgent(logs_dir=logs, assignment_policy='adaptve')
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
    assert ('--capture-data' in environment.invocations[0]) is capture_data
    assert ('--assignment-policy adaptive' in environment.invocations[0]) is (assignment_policy == 'adaptive')
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
    from benchmarks.daydream.agent import DaydreamAgent
    from harbor.models.agent.context import AgentContext
    logs = tmp_path / 'agent'
    logs.mkdir()
    class Environment:
        def is_path_mounted(self, environment_path, host_path):
            assert environment_path == '/logs/agent' and host_path == logs
            return False
        async def exec(self, *args, **kwargs):
            pytest.fail('copied logs must fail before any reviewer setup or invocation')
    agent = DaydreamAgent(logs_dir=logs)
    async def run():
        if phase == 'install':
            await agent.install(Environment())
        else:
            await agent.run('review the pinned comparison', Environment(), AgentContext())
    with pytest.raises(RuntimeError, match='shared.*agent.*log'):
        asyncio.run(run())
    assert not (logs / 'request.json').exists()


@pytest.mark.parametrize('failure', ['namespace', 'whole-login-folder', 'older-build', None, 'candidate'])
def test_install_checks_namespaces_login_mount_and_build(tmp_path, failure):
    from benchmarks.daydream.agent import DaydreamAgent
    commands = []
    logs = tmp_path / 'agent'
    logs.mkdir()
    login = tmp_path / 'reviewer-codex'
    login.mkdir()
    (login / 'auth.json').write_text('{}')
    if failure == 'whole-login-folder':
        (login / 'config.toml').write_text('')
        (login / 'sessions').mkdir()
    # A candidate differs from the pin like an older build, but says it's a candidate.
    older = dict(BUILD, version='0.0.1', source_commit='0' * 40)
    baked = {'older-build': older, 'candidate': dict(older, candidate=True)}.get(failure, BUILD)
    class Environment:
        def is_path_mounted(self, environment_path, host_path):
            return environment_path == '/logs/agent' and host_path == logs
        async def exec(self, command, **kwargs):
            commands.append(command)
            if command == 'unshare -Ur true':
                return SimpleNamespace(return_code=1 if failure == 'namespace' else 0)
            if '/run/reviewer-codex' in command:
                command = command.replace('/run/reviewer-codex', str(login))
                return SimpleNamespace(return_code=subprocess.run(command, shell=True).returncode)
            if command == 'cat /opt/daydream-case/build.json':
                return SimpleNamespace(return_code=0, stdout=json.dumps(dict(baked, python='3.14.3')))
            return SimpleNamespace(return_code=0)
    if failure in (None, 'candidate'):
        asyncio.run(DaydreamAgent(logs_dir=logs).install(Environment()))
        assert len(commands) == 4
        return
    match = {'namespace': 'namespace', 'whole-login-folder': 'only the Codex auth.json',
             'older-build': 'generated for Daydream 0.0.1; regenerate it for the pinned'}[failure]
    with pytest.raises(RuntimeError, match=match):
        asyncio.run(DaydreamAgent(logs_dir=logs).install(Environment()))
    assert len(commands) == {'namespace': 1, 'whole-login-folder': 2, 'older-build': 3}[failure]


@pytest.mark.parametrize('mismatch', ['stale', 'head', 'patch', 'request'])
def test_stale_or_mismatched_inputs_never_invoke_reviewer(review_case, monkeypatch, mismatch):
    from daydream import runner
    case = review_case
    if mismatch == 'stale':
        (case.artifacts / 'findings.json').write_text('{"review_output": []}')
    elif mismatch == 'head':
        case.checkout.head = case.comparison['base_sha']
    elif mismatch == 'patch':
        (case.inputs / 'diff.patch').write_text('a different patch')
    else:
        (case.artifacts / 'daydream/request.json').write_text(json.dumps({'invocation_id': str(uuid.uuid4())}))
    async def unexpected(*args, **kwargs):
        pytest.fail('reviewer must not run for an invalid invocation')
    monkeypatch.setattr(runner, 'run', unexpected)
    assert run_case(case) != 0


def test_sigterm_waits_for_finalization_and_flushes_partial_recorders(monkeypatch):
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


@pytest.mark.parametrize('assignment_policy', [None, 'broad', 'adaptive'])
def test_reviewbench_format_sends_line_findings_and_counts_file_level_ones(review_case, monkeypatch, assignment_policy):
    from dataclasses import dataclass, field
    from daydream import committed_review, review_profile, runner
    from daydream.committed_review_model import CommittedReviewResult
    case = review_case
    pr = {'repo': 'https://github.com/owner/project', 'pr_number': 17, 'base': BASE, 'head': HEAD}
    output = case.artifacts / 'out/findings.json'
    (case.inputs / 'case.json').write_text(json.dumps({'format': 'reviewbench', 'repository': str(case.repo),
        'findings': str(output), 'base': BASE, 'pr': pr, 'description': 'Title\n\nBody'}))
    items = [{'file': 'file.php', 'line': 3, 'summary': 'Off by one', 'description': 'Skips the last row'},
             {'file': 'file.php', 'line': None, 'description': 'A whole-file concern'}]
    async def inference(config, *, review_result_handler, execution):
        assert config.frozen_author_description.head_sha == HEAD
        assert config.frozen_author_description.description == 'Title\n\nBody'
        stage = Path(config.dump_artifacts)
        stage.mkdir(exist_ok=True)
        (stage / 'trajectory.json').write_text(json.dumps({'session_id': str(uuid.uuid4())}))
        review_result_handler(finalized(case, items))
        return 0
    # The pinned build has no assignment policy, so a stand-in default profile carries one.
    @dataclass(frozen=True)
    class Pipeline:
        assignment_policy: str = 'broad'
    @dataclass(frozen=True)
    class Profile:
        pipeline: Pipeline = field(default_factory=Pipeline)
        digest = property(lambda self: 'digest of ' + self.pipeline.assignment_policy)
    sealed_policy = {'name': assignment_policy, 'decomposed': assignment_policy == 'adaptive', 'planned_assignments': 2}
    async def committed(request):
        config = request.config
        assert (request.source, request.base, request.local) == (case.repo, BASE, False)
        assert config.backend == 'codex' and config.output_mode == 'review' and config.flow_name is None
        assert not config.dataset_capture and config.dump_artifacts is None and config.file_config.phases == {}
        assert config.review_profile.profile.pipeline.assignment_policy == assignment_policy
        assert config.review_profile.source_kind == {'broad': 'default', 'adaptive': 'explicit'}[assignment_policy]
        # Committed review builds its Codex input from the process environment.
        assert (Path(os.environ['CODEX_HOME']) / 'auth.json').resolve() == case.inputs / 'credential-store/auth.json'
        assert 'OPENAI_API_KEY' not in os.environ
        # It publishes into the reviewed checkout, where Daydream's own artifacts are allowed.
        terminal = finalized(case, []).terminal_result
        run = case.repo / '.daydream/runs' / terminal['run_id']
        run.mkdir(parents=True)
        (run / 'trajectory.json').write_text(json.dumps({'session_id': terminal['run_id'], 'final_metrics': {
            'total_prompt_tokens': 120, 'total_cached_tokens': 70, 'total_completion_tokens': 11,
            'total_cost_usd': None}}))
        (case.repo / '.review-output.md').write_text('# Review\n')
        sealed = {'run_id': terminal['run_id'], 'terminal_result': terminal, 'findings': [
            {'title': item.get('summary', 'Whole file'), 'payload': {'file': item['file'], 'line': item['line'],
                'claim': item['description'], 'trigger': 'Trigger', 'consequence': 'Consequence', 'grounds': 'Grounds'}}
            for item in items], 'settings': {'assignment_policy': sealed_policy,
                                             'profile_digest': config.review_profile.digest}}
        (case.repo / '.daydream/committed-review.json').write_text(json.dumps(sealed))
        return CommittedReviewResult(json.dumps(sealed), True, 'complete')
    monkeypatch.setenv('OPENAI_API_KEY', 'must-not-reach-codex')
    if assignment_policy:
        monkeypatch.setattr(review_profile, 'build_default_profile', Profile)
    # Both reviews stand ready; the message and the record show which one ran.
    monkeypatch.setattr(committed_review, 'run_committed_review', committed)
    monkeypatch.setattr(runner, 'run', inference)
    assert run_case(case, assignment_policy=assignment_policy) == 0
    assert json.loads(output.read_text()) == {'pr': pr, 'agent': 'daydream', 'findings': [
        {'producer': 'daydream', 'file': 'file.php', 'start_line': 3, 'end_line': 3,
         'message': 'Off by one\n\nSkips the last row' + (
             '\n\nTrigger\n\nConsequence\n\nGrounds' if assignment_policy else '')}]}
    proof = json.loads((case.artifacts / 'daydream/execution.json').read_text())
    # A finding without a line is left out and counted the same way by either review.
    assert proof['status'] == 'complete' and proof['omitted_findings'] == 1
    assert len(proof['retained_findings']) == 2 and proof['assignment_policy'] == assignment_policy
    assert 'CODEX_HOME' not in os.environ
    profile = proof['resolved_configuration']['review_profile']
    if assignment_policy:
        assert proof['runner_exit_code'] == 0 and proof['committed_review'] == {
            'output_installed': True, 'capture_state': 'complete', 'output_error': None,
            'assignment_policy': sealed_policy, 'profile_digest': 'digest of ' + assignment_policy}
        assert profile['digest'] == 'digest of ' + assignment_policy
        assert profile['profile'] == {'pipeline': {'assignment_policy': assignment_policy}}
        # Usage comes from the run's trajectory totals; an unknown cost stays absent.
        assert proof['usage'] == {'prompt_tokens': 120, 'cached_tokens': 70, 'completion_tokens': 11}
        run_id = proof['terminal_result']['run_id']
        assert json.loads((case.logs / 'trajectory.json').read_text())['session_id'] == run_id
        assert sorted(str(path.relative_to(case.logs / 'daydream')) for path in (case.logs / 'daydream').rglob('*')
                      if path.is_file()) == ['committed-review.json', f'runs/{run_id}/trajectory.json', 'trajectory.json']
    else:
        assert proof['committed_review'] is None and len(profile['digest']) == 64


def test_freeze_comparison_records_what_the_image_holds(review_case):
    case = review_case
    for name in ('comparison.json', 'diff.patch'):
        (case.inputs / name).unlink()
    (case.inputs / 'case.json').write_text(json.dumps({'format': 'reviewbench', 'repository': str(case.repo),
                                                      'base': BASE}))
    assert entrypoint.freeze_comparison(case.inputs) == case.comparison
    assert json.loads((case.inputs / 'comparison.json').read_text()) == case.comparison
    assert (case.inputs / 'diff.patch').read_text() == case.checkout.patch
