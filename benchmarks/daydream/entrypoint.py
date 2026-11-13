"""Run one Daydream review inside the task sandbox and write its findings.

Each task's case directory names the repository, where its findings go and in
which benchmark's format; the review itself is the same for every benchmark.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import contextmanager
from dataclasses import asdict, replace
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import subprocess
import tempfile
import time
import uuid

CASE_DIR = Path('/opt/daydream-case')
CODEX_LOGIN_DIR = Path('/run/reviewer-codex')


def _write_json(path: Path, value) -> None:
    from daydream.redaction import redact_structured_text
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(redact_structured_text(json.dumps(value, indent=2, default=str)) + '\n')
    temporary.replace(path)


def verify_build(path: Path) -> dict:
    """Check the baked wheel, dependency lock and Codex CLI against the build."""
    build = json.loads(path.read_text())
    wheel = Path('/opt') / build['wheel_name']
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != build['wheel_sha256']:
        raise ValueError('Daydream wheel hash differs')
    if hashlib.sha256(Path('/opt/reviewer-runtime.lock').read_bytes()).hexdigest() != build['reviewer_runtime_lock_sha256']:
        raise ValueError('Daydream dependency lock differs')
    if importlib.metadata.version('daydream') != build['version']:
        raise ValueError('Installed Daydream version differs')
    actual = subprocess.check_output(['codex', '--version'], text=True).strip()
    if actual != 'codex-cli ' + build['codex_cli']:
        raise ValueError('Codex CLI version differs')
    return build


def _comparison(repository: Path, base: str) -> tuple[dict, str]:
    from daydream import git_ops
    from daydream.deep.artifacts import diff_key
    head = git_ops.head_sha(repository)
    patch = git_ops.diff(repository, base)
    return dict(base_sha=base, head_sha=head,
        merge_base_sha=git_ops.resolve_diff_merge_base(repository, base, head),
        diff_key=diff_key(patch), patch_sha256=hashlib.sha256(patch.encode()).hexdigest()), patch


def freeze_comparison(case_dir: Path) -> dict:
    """Record the comparison a task image holds, for benchmarks that fetch it at build time."""
    case = json.loads((case_dir / 'case.json').read_text())
    comparison, patch = _comparison(Path(case['repository']), case['base'])
    (case_dir / 'comparison.json').write_text(json.dumps(comparison, indent=2) + '\n')
    (case_dir / 'diff.patch').write_text(patch)
    return comparison


def _relative_file(item: dict) -> str:
    file = item.get('file')
    if (not isinstance(file, str) or not file.strip() or '\\' in file
            or PurePosixPath(file).is_absolute() or '..' in PurePosixPath(file).parts):
        raise ValueError('Finalized finding has no repository-relative file context')
    return file


def _description(item: dict) -> str:
    return '\n\n'.join(value.strip() for key in ('description', 'rationale', 'evidence')
                       if isinstance(value := item.get(key), str) and value.strip())


def _line(item: dict) -> int | None:
    line = item.get('line')
    # File-level findings have a null or nonpositive line.
    if line is not None and type(line) is not int:
        raise ValueError('Finalized finding line is malformed')
    return line if line is not None and line > 0 else None


def _aacr_findings(items: list[dict], case: dict) -> tuple[dict, int, int]:
    """AACR accepts file-level findings, so every finding is kept."""
    findings = []
    for item in items:
        file, description, line = _relative_file(item), _description(item), _line(item)
        summary = item.get('summary', item.get('title', ''))
        if not isinstance(summary, str) or not (summary.strip() or description):
            raise ValueError('Finalized finding has no text')
        findings.append(dict(file=file, start_line=line, end_line=line,
                             summary=summary, description=description))
    return {'review_output': findings}, len(findings), 0


def _reviewbench_findings(items: list[dict], case: dict) -> tuple[dict, int, int]:
    """ReviewBench requires a HEAD line, so file-level findings are counted, not sent."""
    findings, omitted = [], 0
    for item in items:
        file, line = _relative_file(item), _line(item)
        if line is None:
            omitted += 1
            continue
        summary = item.get('summary', item.get('title', ''))
        message = '\n\n'.join(text for text in (summary.strip() if isinstance(summary, str) else '',
                                                _description(item)) if text)
        if not message:
            raise ValueError('Finalized finding has no text')
        findings.append(dict(producer='daydream', file=file, start_line=line, end_line=line, message=message))
    return {'pr': case['pr'], 'agent': 'daydream', 'findings': findings}, len(findings), omitted


FORMATS = {'aacr': _aacr_findings, 'reviewbench': _reviewbench_findings}


def _configuration(config, backend, staged: Path) -> dict:
    from daydream.config import PHASE_DEFAULT_MODELS
    from daydream.deep.routing_record import read_routing_record
    from daydream.run_config import _resolved_model, _resolved_reasoning_effort
    return {
        **{name: getattr(config, name) for name in ('backend', 'output_mode', 'flow_name',
            'non_interactive', 'precision_mode', 'dataset_capture', 'dataset_capture_disabled',
            'dataset_store_path', 'trajectory_hub_repo', 'archive', 'run_eval',
            'discover_github_provenance', 'base', 'approved_head_sha', 'model', 'reasoning_effort')},
        'file_config': asdict(config.file_config),
        'review_profile': {'source_kind': config.review_profile.source_kind,
                           'digest': config.review_profile.digest,
                           'profile': asdict(config.review_profile.profile)},
        'phase_defaults': {phase: {'model': _resolved_model(config, phase),
            'reasoning_effort': _resolved_reasoning_effort(config, phase)}
            for phase in sorted(PHASE_DEFAULT_MODELS['codex'])},
        # Read routing from the run's own record: runner.run may replace
        # RunConfig before it routes.
        'latency_routing': read_routing_record(staged / 'deep'),
        'fanout_concurrency': backend.fanout_concurrency,
        'retry_policy': asdict(backend.retry_policy),
    }


def _publish_evidence(staged: Path, logs: Path, run_id: str | None = None) -> None:
    """Copy Daydream's run artifacts, keeping their layout so cross-references resolve."""
    from daydream.trajectory import RUN_DOCUMENT_NAME, run_directory
    evidence = logs / 'daydream'
    evidence.mkdir(parents=True, exist_ok=False)
    trajectory = staged / RUN_DOCUMENT_NAME
    bundle = evidence
    if trajectory.is_file():
        document = json.loads(trajectory.read_bytes())
        session_id = document['session_id']
        if str(uuid.UUID(session_id)) != session_id:
            raise ValueError('Invalid trajectory session ID')
        bundle = run_directory(evidence, session_id)
    else:
        # A committed review keeps a run's trajectory in the run's own folder, which the copy below
        # keeps: the sealed run's, or the only run a review that raised left behind.
        runs = [run_directory(staged, run_id)] if run_id else list(staged.glob('runs/*'))
        if len(runs) == 1:
            trajectory = runs[0] / RUN_DOCUMENT_NAME
    for source in sorted(staged.rglob('*')):
        if source.is_symlink():
            raise ValueError('Unexpected symlink in run artifacts')
        if source.is_file():
            target = bundle / source.relative_to(staged)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    if trajectory.is_file():
        shutil.copyfile(trajectory, evidence / RUN_DOCUMENT_NAME)
        shutil.copyfile(trajectory, logs / RUN_DOCUMENT_NAME)


def _head_line(finding: dict, assignments: list[dict]) -> int | None:
    """A committed finding's line if a source reference places it in the head, where ReviewBench reads lines.

    Daydream admits a line that any reference to the finding's file covers, on either side of
    the change, so a finding can sit on a line that only the old side has.
    """
    def identity(reference: dict) -> dict:
        # What Daydream's ``source_id`` identifies a reference by.
        return {key: value for key, value in reference.items() if key not in ('basis', 'role')}
    payload = finding['payload']
    for reference in payload['evidence_refs']:
        if payload['line'] is None or reference['path'] != payload['file'] or reference['side'] != 'new':
            continue
        # A continuation's reference has a fragment in place of a line range; its assignment
        # summarizes the head lines that fragment holds.
        lines = reference['source_range'] or next((
            part['source_summary'][0]['new'] for part in assignments if part['kind'] == 'continuation'
            and identity(reference) in map(identity, part['source_refs'])), None)
        if lines and lines['start'] <= payload['line'] <= lines['end']:
            return payload['line']
    return None


def _committed_review(sealed, public: Path):
    """A committed review's sealed result as ``runner.run`` hands one over, and what it ran under."""
    from daydream.review_result import FinalizedReview
    from daydream.trajectory import RUN_DOCUMENT_NAME, run_directory
    result = sealed.to_dict()
    # The text of a confirmed finding, labelled as Daydream's own report labels it.
    items = [dict(file=finding['payload']['file'], line=_head_line(finding, result['assignments']),
                  title=finding['title'],
                  description='\n\n'.join([finding['payload']['claim'], *(
                      f'**{label}:** {finding["payload"][key]}' for key, label in (
                          ('trigger', 'Trigger'), ('consequence', 'Consequence'), ('grounds', 'Supporting grounds')))]))
             for finding in result['findings']]
    # Usage is the sealed run's trajectory totals, which the default review reports from the same source.
    trajectory = run_directory(public, result['terminal_result']['run_id']) / RUN_DOCUMENT_NAME
    totals = (json.loads(trajectory.read_bytes()).get('final_metrics') or {}) if trajectory.is_file() else {}
    usage = {key: totals[total] for key, total in (('prompt_tokens', 'total_prompt_tokens'),
        ('completion_tokens', 'total_completion_tokens'), ('cached_tokens', 'total_cached_tokens'),
        ('cost_usd', 'total_cost_usd')) if totals.get(total) is not None}
    return FinalizedReview(result['terminal_result'], items, usage), dict(
        output_installed=sealed.output_installed, capture_state=sealed.capture_state,
        output_error=sealed.output_error, assignment_policy=result['settings']['assignment_policy'],
        profile_digest=result['settings']['profile_digest'])


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


def _check_codex_login(auth: Path) -> None:
    """Admit a refreshable ChatGPT subscription login, never an API key."""
    try:
        value = json.loads(auth.read_text()) if auth.is_file() and not auth.is_symlink() else None
        tokens = value['tokens']
        if value['auth_mode'] != 'chatgpt' or value.get('OPENAI_API_KEY') or not all(
                isinstance(tokens.get(key), str) and tokens[key]
                for key in ('access_token', 'refresh_token', 'account_id', 'id_token')):
            raise ValueError
        payload = tokens['access_token'].split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
        if type(claims.get('exp')) not in (int, float):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise ValueError('The reviewer needs a Codex ChatGPT subscription login, not an API key') from None


@contextmanager
def _codex_home(login: Path, private: Path):
    """A disposable Codex home whose only state is a link to the mounted login."""
    auth = login / 'auth.json'
    _check_codex_login(auth)
    home = private / 'codex'
    home.mkdir(mode=0o700)
    # Codex rewrites the linked file in place, so a refresh reaches the host login.
    (home / 'auth.json').symlink_to(auth)
    (home / 'config.toml').write_text('model_provider = "openai"\ncli_auth_credentials_store = "file"\n')
    yield home


async def review_once(*, case_dir: Path, artifacts: Path, agent_logs: Path,
                      invocation_id: str, credential_home: Path, capture_data: bool = False,
                      assignment_policy: str | None = None) -> int:
    """Run the complete pipeline once; write findings only if it finishes cleanly.

    With an assignment policy the review is Daydream's committed review under its
    default profile with that policy. A build that can't run it fails the trial.
    """
    from daydream import git_ops, runner
    from daydream.backends import BackendExecutionInput
    from daydream.config_file import DaydreamFileConfig
    from daydream.git_ops import StaticGitHubAuth
    from daydream.github_app import GitHubExecutionInput
    from daydream.review_profile import ResolvedProfile, build_default_profile
    from daydream.review_result import validate_terminal_result
    from daydream.run_config import FrozenAuthorDescription, RunConfig
    from daydream.runner import RunnerExecutionInput

    started = time.monotonic()
    proof = artifacts / 'daydream'
    execution_path = proof / 'execution.json'
    prior_execution = execution_path.exists()
    record = dict(schema_version=1, invocation_id=invocation_id, status='failed',
        runner_exit_code=None, terminal_result=None, analyzed_comparison={}, post_comparison={},
        usage={}, retained_findings=[], omitted_findings=0, resolved_configuration={}, build={},
        findings_sha256=None, diagnostics=[], elapsed_seconds=0,
        assignment_policy=assignment_policy, committed_review=None,
        cost_interpretation='API-equivalent estimate when available; subscription charges unknown')
    try:
        if str(uuid.UUID(invocation_id)) != invocation_id:
            raise ValueError('Invalid invocation identity')
        case = json.loads((case_dir / 'case.json').read_text())
        repository, output, translate = Path(case['repository']), Path(case['findings']), FORMATS[case['format']]
        if prior_execution or output.exists() or (agent_logs / 'daydream').exists():
            raise ValueError('Prior reviewer artifacts exist; use a fresh trial')
        if json.loads((proof / 'request.json').read_text()) != {'invocation_id': invocation_id}:
            raise ValueError('Invocation request identity differs')
        committed = assignment_policy is not None
        if committed and capture_data:
            raise ValueError('Committed review keeps no data capture; run it with capture_data=false')
        expected = json.loads((case_dir / 'comparison.json').read_text())
        record['build'] = json.loads((case_dir / 'build.json').read_text())
        actual, patch = _comparison(repository, expected['base_sha'])
        record['analyzed_comparison'] = actual
        if actual != expected or patch.encode() != (case_dir / 'diff.patch').read_bytes():
            raise ValueError('Pinned comparison differs from repository')
        if git_ops.status_porcelain(repository).strip():
            raise ValueError('Reviewer repository is not clean')
        description = case.get('description')
        finalized = []
        with tempfile.TemporaryDirectory(prefix='daydream-review-') as temporary:
            private = Path(temporary)
            staged = private / 'run-artifacts'
            staged.mkdir()
            with (_isolated_environment(private) as controls,
                  _codex_home(credential_home, private) as role_home):
                controls['CODEX_HOME'] = str(role_home)
                backend = BackendExecutionInput.from_environment(controls, backend='codex')
                default = profile = build_default_profile()
                if committed:
                    # A build whose profile has no assignment policy, such as 0.34.0, fails here.
                    profile = replace(default, pipeline=replace(default.pipeline, assignment_policy=assignment_policy))
                    # Committed review publishes into the reviewed checkout and takes no other destination.
                    staged = repository / '.daydream'
                config = RunConfig(target=str(repository), backend='codex', output_mode='review',
                    flow_name=None if committed else 'review', non_interactive=True, precision_mode=False,
                    dataset_capture=capture_data, dataset_capture_disabled=not capture_data,
                    dataset_store_path=agent_logs / 'dataset' if capture_data else None,
                    archive=False, run_eval=False,
                    discover_github_provenance=False, base=expected['base_sha'],
                    approved_head_sha=expected['head_sha'], file_config=DaydreamFileConfig(),
                    review_profile=ResolvedProfile(profile=profile,
                        source_kind='default' if profile == default else 'explicit'),
                    dump_artifacts=None if committed else str(staged),
                    frozen_author_description=FrozenAuthorDescription(expected['head_sha'], description)
                        if description else None)
                execution = RunnerExecutionInput(backend, GitHubExecutionInput(auth=StaticGitHubAuth(controls)),
                    backends={'codex': backend})
                try:
                    if committed:
                        from daydream.committed_review import CommittedReviewRequest, run_committed_review
                        # Committed review reads Codex's settings from the process environment.
                        os.environ['CODEX_HOME'] = str(role_home)
                        sealed = await run_committed_review(CommittedReviewRequest(
                            repository, expected['base_sha'], config=config))
                        review, record['committed_review'] = _committed_review(sealed, staged)
                        finalized.append(review)
                        record['runner_exit_code'] = 0 if sealed.output_installed else 1
                    else:
                        record['runner_exit_code'] = await runner.run(config,
                            review_result_handler=finalized.append, execution=execution)
                except BaseException as error:
                    record['status'] = 'cancelled' if isinstance(error, asyncio.CancelledError) else 'failed'
                    record['diagnostics'].append('Daydream raised ' + type(error).__name__)
                finally:
                    record['resolved_configuration'] = _configuration(config, backend, staged)
                    if finalized:
                        result = finalized[-1]
                        record.update(terminal_result=result.terminal_result, usage=result.usage,
                                      retained_findings=result.items)
                    record['post_comparison'], _ = _comparison(repository, expected['base_sha'])
                    _publish_evidence(staged, agent_logs,
                                      record['terminal_result']['run_id'] if committed and finalized else None)
                if len(finalized) != 1:
                    raise ValueError('Expected exactly one finalized review')
                terminal = record['terminal_result']
                validate_terminal_result(terminal, expected_head_sha=expected['head_sha'])
                revision = terminal['analyzed_revision']
                if any(revision.get(key) != expected[key] for key in ('head_sha', 'merge_base_sha', 'diff_key')):
                    raise ValueError('Analyzed revision differs from the pinned comparison')
                if record['post_comparison'] != expected:
                    raise ValueError('Reviewer changed the pinned comparison')
                from daydream.deep.orchestrator import _has_non_daydream_worktree_changes
                if _has_non_daydream_worktree_changes(git_ops.status_porcelain(repository)):
                    raise ValueError('Reviewer changed files outside its review artifacts')
                if terminal['analysis_state'] != 'complete' or terminal['pipeline_state'] != 'completed':
                    record['status'] = 'incomplete'
                    raise ValueError('Review did not complete analysis')
                if record['runner_exit_code'] != 0 or record['diagnostics']:
                    raise ValueError('Daydream did not exit successfully')
                if not (agent_logs / 'trajectory.json').is_file():
                    raise ValueError('Trajectory unavailable')
                ran = record['committed_review']
                if committed and (ran['assignment_policy']['name'], ran['profile_digest']) != (
                        assignment_policy, config.review_profile.digest):
                    raise ValueError('Committed review ran under another policy or profile')
                findings, count, record['omitted_findings'] = translate(record['retained_findings'], case)
                _write_json(output, findings)
                record['findings_sha256'] = hashlib.sha256(output.read_bytes()).hexdigest()
                record['status'] = 'complete' if count else 'completed_empty'
        return 0
    except BaseException as error:
        record['diagnostics'].append(type(error).__name__ + ': ' + str(error))
        return 130 if record['status'] == 'cancelled' else 1
    finally:
        record['elapsed_seconds'] = time.monotonic() - started
        if not prior_execution:
            _write_json(execution_path, record)


async def _main(invocation: str, capture_data: bool = False, assignment_policy: str | None = None) -> int:
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
                result = await review_once(case_dir=CASE_DIR, artifacts=Path('/logs/artifacts'),
                    agent_logs=Path('/logs/agent'), invocation_id=invocation,
                    credential_home=CODEX_LOGIN_DIR, capture_data=capture_data,
                    assignment_policy=assignment_policy)
            finally:
                loop.remove_signal_handler(signal.SIGTERM)
    except asyncio.CancelledError:
        return 130
    return 130 if requested else result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--invocation', help='Review once for this host-issued invocation')
    mode.add_argument('--freeze-comparison', action='store_true',
                      help="Record the task image's comparison in its case directory")
    parser.add_argument('--capture-data', action='store_true',
                        help="Keep Daydream's JSONL data capture in the trial's agent logs")
    parser.add_argument('--assignment-policy', choices=('broad', 'adaptive'),
                        help="Review through Daydream's committed review under this assignment policy")
    args = parser.parse_args()
    if args.freeze_comparison:
        print(json.dumps(freeze_comparison(CASE_DIR)))
    else:
        raise SystemExit(asyncio.run(_main(args.invocation, args.capture_data, args.assignment_policy)))
