"""One sandbox-side native Daydream review, with fail-closed finalized output."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid


DAYDREAM_REVISION = '5bae1ac09918ecf8d095aaf6d283235ca1ad83a8'


def _write_json(path: Path, value) -> None:
    from daydream.redaction import redact_structured_text
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(redact_structured_text(json.dumps(value, indent=2, default=str)) + '\n')
    temporary.replace(path)


def verify_build(path: Path) -> dict:
    """Check the baked wheel, dependency lock, and native CLI before admission."""
    build = json.loads(path.read_text())
    if build['source_commit'] != DAYDREAM_REVISION:
        raise ValueError('Daydream source pin differs')
    wheel = Path('/opt') / build['wheel_name']
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != build['wheel_sha256']:
        raise ValueError('Daydream wheel hash differs')
    if hashlib.sha256(Path('/opt/reviewer-runtime.lock').read_bytes()).hexdigest() != build['reviewer_runtime_lock_sha256']:
        raise ValueError('Daydream dependency lock differs')
    if importlib.metadata.version('daydream') != build['version']:
        raise ValueError('Installed Daydream version differs')
    actual = subprocess.check_output(['codex', '--version'], text=True).strip()
    if actual != 'codex-cli ' + build['codex_cli']:
        raise ValueError('Native Codex CLI version differs')
    return build


def _comparison(repository: Path, base: str) -> tuple[dict, str]:
    from daydream import git_ops
    from daydream.deep.artifacts import diff_key
    head = git_ops.head_sha(repository)
    patch = git_ops.diff(repository, base)
    return dict(base_sha=base, head_sha=head,
        merge_base_sha=git_ops.resolve_diff_merge_base(repository, base, head),
        diff_key=diff_key(patch), patch_sha256=hashlib.sha256(patch.encode()).hexdigest()), patch


def _translate(items: list[dict]) -> dict:
    """AACR admits file-level findings; impose no new-side diff-anchor policy."""
    findings = []
    for item in items:
        file = item.get('file')
        if (not isinstance(file, str) or not file.strip() or '\\' in file
                or PurePosixPath(file).is_absolute() or '..' in PurePosixPath(file).parts):
            raise ValueError('Finalized finding has no repository-relative file context')
        description = '\n\n'.join(value.strip() for key in ('description', 'rationale', 'evidence')
            if isinstance(value := item.get(key), str) and value.strip())
        summary = item.get('summary', item.get('title', ''))
        if not isinstance(summary, str) or not (summary.strip() or description):
            raise ValueError('Finalized finding has no text')
        line = item.get('line')
        # Native file-level locations may use null or a nonpositive hint.
        if line is not None and type(line) is not int:
            raise ValueError('Finalized finding line is malformed')
        line = line if line is not None and line > 0 else None
        findings.append(dict(file=file, start_line=line, end_line=line,
                             summary=summary, description=description))
    return {'review_output': findings}


def _configuration(config, backend, staged: Path) -> dict:
    from daydream.config import PHASE_DEFAULT_MODELS
    from daydream.deep.routing_record import read_routing_record
    from daydream.run_config import _resolved_model, _resolved_reasoning_effort
    return {
        **{name: getattr(config, name) for name in ('backend', 'output_mode', 'flow_name',
            'non_interactive', 'precision_mode', 'dataset_capture_disabled', 'archive', 'run_eval',
            'discover_github_provenance', 'base', 'approved_head_sha', 'model', 'reasoning_effort')},
        'file_config': asdict(config.file_config),
        'review_profile': {'source_kind': config.review_profile.source_kind,
                           'profile': asdict(config.review_profile.profile)},
        'phase_defaults': {phase: {'model': _resolved_model(config, phase),
            'reasoning_effort': _resolved_reasoning_effort(config, phase)}
            for phase in sorted(PHASE_DEFAULT_MODELS['codex'])},
        # runner.run may replace RunConfig before selecting routes. The native
        # artifact owns the actual wonder decision and per-group arbiter efforts.
        'latency_routing': read_routing_record(staged / 'deep'),
        'fanout_concurrency': backend.fanout_concurrency,
        'retry_policy': asdict(backend.retry_policy),
    }


def _publish_evidence(staged: Path, logs: Path) -> None:
    """Keep the native run layout so joined sibling references still resolve."""
    from daydream.trajectory import RUN_DOCUMENT_NAME, run_directory
    evidence = logs / 'daydream'
    evidence.mkdir(parents=True, exist_ok=False)
    trajectory = staged / RUN_DOCUMENT_NAME
    bundle = evidence
    if trajectory.is_file():
        document = json.loads(trajectory.read_bytes())
        session_id = document['session_id']
        if str(uuid.UUID(session_id)) != session_id:
            raise ValueError('Invalid native trajectory identity')
        bundle = run_directory(evidence, session_id)
    for source in sorted(staged.rglob('*')):
        if source.is_symlink():
            raise ValueError('Unexpected native evidence symlink')
        if source.is_file():
            target = bundle / source.relative_to(staged)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    if trajectory.is_file():
        shutil.copyfile(trajectory, evidence / RUN_DOCUMENT_NAME)
        shutil.copyfile(trajectory, logs / RUN_DOCUMENT_NAME)


@contextmanager
def _isolated_environment(private: Path):
    previous = dict(os.environ)
    controls = {key: value for key, value in previous.items()
        if key in {'PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}}
    os.environ.clear()
    os.environ.update(controls, DAYDREAM_ARCHIVE_DIR=str(private / 'archive'))
    try:
        yield controls
    finally:
        os.environ.clear()
        os.environ.update(previous)


async def review_once(*, repository: Path, inputs: Path, artifacts: Path, agent_logs: Path,
                      invocation_id: str, credential_home: Path) -> int:
    """Invoke the complete pipeline once; publish only after successful finalization."""
    from daydream import git_ops, runner
    from daydream.backends import BackendExecutionInput
    from daydream.benchmark.reviewbench.harbor_auth import reviewer_credentials
    from daydream.config_file import DaydreamFileConfig
    from daydream.git_ops import StaticGitHubAuth
    from daydream.github_app import GitHubExecutionInput
    from daydream.review_profile import resolve_harbor_profile
    from daydream.review_result import validate_terminal_result
    from daydream.run_config import RunConfig
    from daydream.runner import RunnerExecutionInput

    started = time.monotonic()
    proof = artifacts / 'daydream'
    output = artifacts / 'findings.json'
    execution_path = proof / 'execution.json'
    prior_execution = execution_path.exists()
    record = dict(schema_version=1, invocation_id=invocation_id, status='failed',
        runner_exit_code=None, terminal_result=None, analyzed_comparison={}, post_comparison={},
        usage={}, retained_findings=[], resolved_configuration={}, build={}, findings_sha256=None,
        diagnostics=[], elapsed_seconds=0,
        cost_interpretation='API-equivalent estimate when available; subscription charges unknown')
    try:
        if str(uuid.UUID(invocation_id)) != invocation_id:
            raise ValueError('Invalid invocation identity')
        if prior_execution or output.exists() or (agent_logs / 'daydream').exists():
            raise ValueError('Prior reviewer artifacts exist; use a fresh trial')
        if json.loads((proof / 'request.json').read_text()) != {'invocation_id': invocation_id}:
            raise ValueError('Invocation request identity differs')
        expected = json.loads((inputs / 'comparison.json').read_text())
        record['build'] = json.loads((inputs / 'build.json').read_text())
        actual, patch = _comparison(repository, expected['base_sha'])
        record['analyzed_comparison'] = actual
        if actual != expected or patch.encode() != (inputs / 'diff.patch').read_bytes():
            raise ValueError('Pinned comparison differs from repository')
        if git_ops.status_porcelain(repository).strip():
            raise ValueError('Reviewer repository is not clean')
        finalized = []
        with tempfile.TemporaryDirectory(prefix='aacr-daydream-') as temporary:
            private = Path(temporary)
            staged = private / 'native-evidence'
            staged.mkdir()
            with (_isolated_environment(private) as controls,
                  reviewer_credentials(credential_home, private / 'codex', backend='codex') as role_home):
                (role_home / 'config.toml').write_text('model_provider = "openai"\ncli_auth_credentials_store = "file"\n')
                controls['CODEX_HOME'] = str(role_home)
                backend = BackendExecutionInput.from_environment(controls, backend='codex')
                config = RunConfig(target=str(repository), backend='codex', output_mode='review',
                    flow_name='review', non_interactive=True, precision_mode=False,
                    dataset_capture_disabled=True, archive=False, run_eval=False,
                    discover_github_provenance=False, base=expected['base_sha'],
                    approved_head_sha=expected['head_sha'], file_config=DaydreamFileConfig(),
                    review_profile=resolve_harbor_profile(env={}), dump_artifacts=str(staged))
                execution = RunnerExecutionInput(backend, GitHubExecutionInput(auth=StaticGitHubAuth(controls)),
                    backends={'codex': backend})
                try:
                    record['runner_exit_code'] = await runner.run(config,
                        review_result_handler=finalized.append, execution=execution)
                except BaseException as error:
                    record['status'] = 'cancelled' if isinstance(error, asyncio.CancelledError) else 'failed'
                    record['diagnostics'].append('Native invocation raised ' + type(error).__name__)
                finally:
                    record['resolved_configuration'] = _configuration(config, backend, staged)
                    if finalized:
                        result = finalized[-1]
                        record.update(terminal_result=result.terminal_result, usage=result.usage,
                                      retained_findings=result.items)
                    record['post_comparison'], _ = _comparison(repository, expected['base_sha'])
                    _publish_evidence(staged, agent_logs)
                if len(finalized) != 1:
                    raise ValueError('Expected exactly one finalized native review')
                terminal = record['terminal_result']
                validate_terminal_result(terminal, expected_head_sha=expected['head_sha'])
                revision = terminal['analyzed_revision']
                if any(revision.get(key) != expected[key] for key in ('head_sha', 'merge_base_sha', 'diff_key')):
                    raise ValueError('Native analyzed revision differs from pinned comparison')
                if record['post_comparison'] != expected:
                    raise ValueError('Reviewer changed the pinned comparison')
                from daydream.deep.orchestrator import _has_non_daydream_worktree_changes
                if _has_non_daydream_worktree_changes(git_ops.status_porcelain(repository)):
                    raise ValueError('Reviewer changed files outside native review artifacts')
                if terminal['analysis_state'] != 'complete' or terminal['pipeline_state'] != 'completed':
                    record['status'] = 'incomplete'
                    raise ValueError('Native review did not complete analysis')
                if record['runner_exit_code'] != 0 or record['diagnostics']:
                    raise ValueError('Native invocation did not exit successfully')
                if not (agent_logs / 'trajectory.json').is_file():
                    raise ValueError('Native trajectory unavailable')
                findings = _translate(record['retained_findings'])
                _write_json(output, findings)
                record['findings_sha256'] = hashlib.sha256(output.read_bytes()).hexdigest()
                record['status'] = 'complete' if findings['review_output'] else 'completed_empty'
        return 0
    except BaseException as error:
        record['diagnostics'].append(type(error).__name__ + ': ' + str(error))
        return 130 if record['status'] == 'cancelled' else 1
    finally:
        record['elapsed_seconds'] = time.monotonic() - started
        if not prior_execution:
            _write_json(execution_path, record)


async def _main(invocation: str) -> int:
    import anyio
    from daydream.trajectory import flush_active_signal_recorders

    loop = asyncio.get_running_loop()
    requested = False
    result = 130
    try:
        with anyio.CancelScope() as shutdown:
            def interrupt():
                nonlocal requested
                if not requested:
                    requested = True
                    shutdown.cancel()
                    flush_active_signal_recorders()
            loop.add_signal_handler(signal.SIGTERM, interrupt)
            try:
                result = await review_once(repository=Path('/workspace/repo'), inputs=Path('/opt/aacr'),
                    artifacts=Path('/logs/artifacts'), agent_logs=Path('/logs/agent'),
                    invocation_id=invocation, credential_home=Path('/run/aacr-codex'))
            finally:
                loop.remove_signal_handler(signal.SIGTERM)
    except asyncio.CancelledError:
        return 130
    return 130 if requested else result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--invocation', required=True)
    raise SystemExit(asyncio.run(_main(parser.parse_args().invocation)))
