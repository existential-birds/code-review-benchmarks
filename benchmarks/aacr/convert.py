"""Map the pinned Laravel AACR review case to one native Harbor Oracle task."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib

from benchmarks.aacr.upstream import repo_utils, schema
from benchmarks.aacr.upstream.converters.aacr_bench import convert_record

AACR_REVISION = 'ee9d7fef70ef31acea2ff7ae316ce0a9575fe4df'
DATASET_SHA256 = '02c8561f1c3994a26f427d479b2a3f6e6c8e6858161fbc903b45248fa2cb2097'
PR_URL = 'https://github.com/laravel/framework/pull/56591'
BASE = '6d94b16fd5f4f72bb741f9b900b83179c8ab46c9'
HEAD = '0a5238acfc4ca99c363283bf3bb16e20905f2a39'
TASK_NAME = 'laravel-framework-56591'
PACKAGE = Path(__file__).resolve().parent
DAYDREAM_BUILD = json.loads((PACKAGE / 'daydream-build.json').read_text())


def render_task(record: dict, destination: Path) -> schema.ReviewInstance:
    """Reuse AACR's converter/schema and keep annotations only in verifier assets."""
    instance = convert_record(record)
    if instance is None:
        raise ValueError('AACR converter rejected the selected review case')
    # Round trip through the upstream loader before writing any task assets.
    with tempfile.TemporaryDirectory(prefix='aacr-schema-') as temporary:
        standard = Path(temporary) / 'instance.jsonl'
        standard.write_text(json.dumps(instance.to_dict()) + '\n')
        instance, = schema.load_instances(standard)
    destination = Path(destination)
    shutil.copytree(PACKAGE / 'templates', destination)
    metadata = {
        'instance_id': instance.instance_id,
        'repository': instance.repo,
        'pr_url': record['githubPrUrl'],
        'language': record['project_main_language'],
        'base_commit': instance.base_commit,
        'head_commit': instance.head_commit,
        'aacr_revision': AACR_REVISION,
        'dataset_sha256': DATASET_SHA256,
    }
    config = (destination / 'task.toml').read_text()
    config += '\n[metadata]\n' + ''.join(f'{key} = {json.dumps(value)}\n' for key, value in metadata.items())
    (destination / 'task.toml').write_text(config)
    (destination / 'tests/instance.jsonl').write_text(json.dumps(instance.to_dict()) + '\n')
    suite = destination / 'tests/suite'
    runtime = suite / 'benchmarks/aacr'
    runtime.mkdir(parents=True)
    for name in ('__init__.py', 'score.py', 'transport.py', 'pi_judge.mjs', 'package.json', 'package-lock.json'):
        shutil.copy2(PACKAGE / name, runtime / name)
    shutil.copytree(PACKAGE / 'upstream', runtime / 'upstream', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copy2(PACKAGE.parent / '__init__.py', suite / 'benchmarks/__init__.py')
    for name in ('pyproject.toml', 'README.md', 'LICENSE'):
        shutil.copy2(PACKAGE.parents[1] / name, suite / name)
    return instance


def prepare_context(instance: schema.ReviewInstance, task: Path) -> Path:
    """Run upstream's destructive preparation only in a fresh generated directory."""
    environment = Path(task) / 'environment'
    environment.mkdir(parents=True, exist_ok=True)
    repository = environment / 'repo'
    if repository.exists():
        raise FileExistsError(f'Refusing to replace repository context: {repository}')
    with tempfile.TemporaryDirectory(prefix='.aacr-context-', dir=environment) as temporary:
        prepared = repo_utils.prepare_repo(
            Path(temporary), instance.resolved_clone_url, instance.repo,
            instance.base_commit, instance.head_commit,
        )
        for revision in (instance.base_commit, instance.head_commit):
            subprocess.run(['git', '-C', str(prepared), 'cat-file', '-e', revision + '^{commit}'], check=True)
        actual = subprocess.check_output(['git', '-C', str(prepared), 'rev-parse', 'HEAD'], text=True).strip()
        status = subprocess.check_output(['git', '-C', str(prepared), 'status', '--porcelain'], text=True)
        if actual != instance.head_commit or status:
            raise ValueError('Repository preparation did not produce a clean pinned head')
        prepared.rename(repository)
    return repository


def configure_daydream_task(task: Path, wheel: Path) -> None:
    """Add only the installed-reviewer gap to an ordinary isolated Harbor task."""
    from daydream import git_ops
    from daydream.benchmark.reviewbench.harbor_build import NODE_IMAGE, reviewer_build
    from daydream.deep.artifacts import diff_key

    if hashlib.sha256(wheel.read_bytes()).hexdigest() != DAYDREAM_BUILD['wheel_sha256']:
        raise ValueError('Daydream wheel SHA-256 differs from the baseline build')
    if wheel.name != DAYDREAM_BUILD['wheel_name']:
        raise ValueError('Daydream wheel filename differs from the baseline build')
    suffix, assets, identity = reviewer_build(wheel, platform='linux/arm64')
    identity.update(DAYDREAM_BUILD)
    identity['harbor_revision'] = '6099b8cf2bfbbb3e7ff1eb2163bfc4524dbd9ba5'
    environment = task / 'environment'
    metadata = tomllib.loads((task / 'task.toml').read_text())['metadata']
    repo = environment / 'repo'
    base, head = metadata['base_commit'], metadata['head_commit']
    if git_ops.head_sha(repo) != head or git_ops.status_porcelain(repo).strip():
        raise ValueError('Daydream task requires a clean pinned head')
    patch = git_ops.diff(repo, base)
    comparison = dict(base_sha=base, head_sha=head,
        merge_base_sha=git_ops.merge_base(repo, base, head), diff_key=diff_key(patch),
        patch_sha256=hashlib.sha256(patch.encode()).hexdigest())
    inputs = environment / 'aacr'
    inputs.mkdir()
    (inputs / 'comparison.json').write_text(json.dumps(comparison, indent=2) + '\n')
    (inputs / 'diff.patch').write_text(patch)
    (inputs / 'build.json').write_text(json.dumps(identity, indent=2) + '\n')
    shutil.copy2(inputs / 'comparison.json', task / 'tests/comparison.json')
    shutil.copy2(inputs / 'build.json', task / 'tests/build.json')
    # Reference annotations and the independent judge transport remain verifier-only.
    runtime = environment / 'suite/benchmarks/aacr'
    runtime.mkdir(parents=True)
    shutil.copy2(PACKAGE.parent / '__init__.py', runtime.parent / '__init__.py')
    for name in ('__init__.py', 'daydream_entrypoint.py'):
        shutil.copy2(PACKAGE / name, runtime / name)
    for directory in (environment, task / 'tests'):
        for name, contents in assets.items():
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
    (environment / 'Dockerfile').write_text(
        f'FROM {NODE_IMAGE}\n'
        'RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates ripgrep '
        '&& rm -rf /var/lib/apt/lists/*\n' + suffix +
        'COPY repo/ /workspace/repo/\nCOPY aacr/ /opt/aacr/\n'
        'COPY suite/ /opt/aacr-suite/\n'
        'RUN cp -r /opt/aacr-suite/benchmarks /opt/daydream/lib/python3.14/site-packages/\n'
        'WORKDIR /workspace/repo\n')
    (environment / 'docker-compose.yaml').write_text(
        'services:\n  main:\n    security_opt:\n      - seccomp=unconfined\n'
        '    volumes:\n      - type: bind\n'
        '        source: ${AACR_CODEX_AUTH_DIR:?set the dedicated native Codex reviewer directory}\n'
        '        target: /run/aacr-codex\n        bind:\n          create_host_path: false\n')
    # Keep Pi SDK/provider/model locked exactly as in the Oracle verifier.
    (task / 'tests/Dockerfile').write_text(
        f'FROM {NODE_IMAGE}\nRUN apt-get update && apt-get install -y --no-install-recommends '
        'git ca-certificates && rm -rf /var/lib/apt/lists/*\n' + suffix +
        'COPY . /tests/\nRUN uv pip install --no-deps --python /opt/daydream/bin/python /tests/suite '
        '&& npm ci --omit=dev --prefix /tests/suite/benchmarks/aacr\nWORKDIR /tests/suite\n')
    script = (task / 'tests/test.sh').read_text().replace(
        '--output /logs/verifier', '--output /logs/verifier \\\n  --daydream-proof /logs/artifacts/daydream \\\n  --comparison /tests/comparison.json --host-receipt /logs/agent/host-receipt.json')
    (task / 'tests/test.sh').write_text(script)
    config = (task / 'task.toml').read_text().replace('timeout_sec = 60.0', 'timeout_sec = 5400.0')
    config = config.replace('network_mode = "no-network"', 'network_mode = "public"')
    config = config.replace('cpus = 1', 'cpus = 4').replace('memory_mb = 2048', 'memory_mb = 8192')
    config = config.replace('fixed Oracle reviewer', 'complete Daydream reviewer').replace('"oracle"', '"daydream"')
    # Transfer only this nonsecret log document so host cancellation remains
    # authoritative even if the sandbox receipt upload fails. Harbor lint's
    # artifact-log-overlap warning is intentional for this single file.
    config = 'artifacts = ["/logs/agent/host-receipt.json"]\n' + config
    config += ''.join(f'daydream_{key} = {json.dumps(identity[key])}\n'
        for key in ('source_commit', 'wheel_sha256', 'codex_cli', 'reviewer_runtime_lock_sha256'))
    (task / 'task.toml').write_text(config)
    shutil.rmtree(task / 'solution')


def generate_task(upstream: Path, output: Path, *, daydream_wheel: Path | None = None) -> Path:
    """Validate pinned AACR source/data and generate exactly one isolated task."""
    upstream = Path(upstream).resolve()
    revision = subprocess.run(['git', '-C', str(upstream), 'rev-parse', 'HEAD'], text=True, capture_output=True)
    if revision.returncode or revision.stdout.strip() != AACR_REVISION:
        raise ValueError(f'Expected AACR revision {AACR_REVISION}')
    status = subprocess.check_output(['git', '-C', str(upstream), 'status', '--porcelain'], text=True)
    if status:
        raise ValueError('Expected a clean AACR checkout; preserve operator changes')
    dataset = upstream / 'dataset/positive_samples.json'
    if hashlib.sha256(dataset.read_bytes()).hexdigest() != DATASET_SHA256:
        raise ValueError('AACR positive dataset SHA-256 does not match the pin')
    records = [record for record in json.loads(dataset.read_text()) if record.get('githubPrUrl') == PR_URL]
    if len(records) != 1 or records[0]['source_commit'] != BASE or records[0]['target_commit'] != HEAD:
        raise ValueError('Expected exactly the pinned Laravel review case')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    destination = output / TASK_NAME
    if destination.exists():
        raise FileExistsError(f'Refusing to replace generated task: {destination}')
    with tempfile.TemporaryDirectory(prefix='.aacr-task-', dir=output) as temporary:
        stage = Path(temporary) / TASK_NAME
        instance = render_task(records[0], stage)
        prepare_context(instance, stage)
        if daydream_wheel is not None:
            configure_daydream_task(stage, daydream_wheel)
        stage.rename(destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', required=True, type=Path, help='Clean checkout of the pinned Alibaba AACR source')
    parser.add_argument('--output', type=Path, default=Path('artifacts/aacr/tasks'))
    parser.add_argument('--daydream-wheel', type=Path, help='Use the pinned Daydream baseline instead of Oracle')
    arguments = parser.parse_args()
    print(generate_task(arguments.upstream, arguments.output, daydream_wheel=arguments.daydream_wheel))


if __name__ == '__main__':
    main()
