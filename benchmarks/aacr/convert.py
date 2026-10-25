"""Map the pinned Laravel AACR review case to one native Harbor Oracle task."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from benchmarks.aacr.upstream import repo_utils, schema
from benchmarks.aacr.upstream.converters.aacr_bench import convert_record

AACR_REVISION = 'ee9d7fef70ef31acea2ff7ae316ce0a9575fe4df'
DATASET_SHA256 = '02c8561f1c3994a26f427d479b2a3f6e6c8e6858161fbc903b45248fa2cb2097'
PR_URL = 'https://github.com/laravel/framework/pull/56591'
BASE = '6d94b16fd5f4f72bb741f9b900b83179c8ab46c9'
HEAD = '0a5238acfc4ca99c363283bf3bb16e20905f2a39'
TASK_NAME = 'laravel-framework-56591'
PACKAGE = Path(__file__).resolve().parent


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


def generate_task(upstream: Path, output: Path) -> Path:
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
        stage.rename(destination)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', required=True, type=Path, help='Clean checkout of the pinned Alibaba AACR source')
    parser.add_argument('--output', type=Path, default=Path('artifacts/aacr/tasks'))
    arguments = parser.parse_args()
    print(generate_task(arguments.upstream, arguments.output))


if __name__ == '__main__':
    main()
