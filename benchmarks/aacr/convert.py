"""Convert the pinned AACR-Bench dataset into Harbor review tasks."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tomllib
from unittest import mock

from benchmarks.aacr.upstream import repo_utils, schema
from benchmarks.aacr.upstream.converters.aacr_bench import convert_record
from benchmarks.daydream import build
from benchmarks.daydream.verifier import HOST_RECEIPT

AACR_REVISION = 'ee9d7fef70ef31acea2ff7ae316ce0a9575fe4df'
DATASET_SHA256 = '02c8561f1c3994a26f427d479b2a3f6e6c8e6858161fbc903b45248fa2cb2097'
PR_URL = 'https://github.com/laravel/framework/pull/56591'
BASE = '6d94b16fd5f4f72bb741f9b900b83179c8ab46c9'
HEAD = '0a5238acfc4ca99c363283bf3bb16e20905f2a39'
PACKAGE = Path(__file__).resolve().parent
HOLDOUT = PACKAGE / 'holdout.txt'


def task_name(record: dict) -> str:
    """Keep stable PR identities independent of upstream's commit-based instance IDs."""
    match = re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([0-9]+)',
                         record.get('githubPrUrl', ''))
    if match is None:
        raise ValueError('Expected a GitHub pull request URL for the review case')
    return re.sub(r'[^a-z0-9]+', '-', '-'.join(match.groups()).lower()).strip('-')


def validated_instance(record: dict) -> schema.ReviewInstance:
    instance = convert_record(record)
    if instance is None:
        raise ValueError('AACR converter rejected the review case')
    return schema.ReviewInstance.from_dict(instance.to_dict(), task_name(record))


def render_task(record: dict, destination: Path) -> schema.ReviewInstance:
    """Reuse AACR's converter/schema and keep annotations only in verifier assets."""
    instance = validated_instance(record)
    destination = Path(destination)
    shutil.copytree(PACKAGE / 'templates', destination)
    oracle = (record['githubPrUrl'] == PR_URL and
              instance.base_commit == BASE and instance.head_commit == HEAD)
    if not oracle:
        shutil.rmtree(destination / 'solution')
    instruction = (destination / 'instruction.md').read_text().format(
        pr_url=record['githubPrUrl'], base_commit=instance.base_commit, head_commit=instance.head_commit)
    (destination / 'instruction.md').write_text(instruction)
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
    config = (destination / 'task.toml').read_text().format(task_name=task_name(record))
    if oracle:
        config = config.replace('review case and subscription judge',
                                'review case with a fixed Oracle reviewer and subscription judge')
        config = config.replace('["code-review", "aacr"]', '["code-review", "aacr", "oracle"]')
    config += '\n[metadata]\n' + ''.join(f'{key} = {json.dumps(value)}\n' for key, value in metadata.items())
    (destination / 'task.toml').write_text(config)
    (destination / 'tests/instance.jsonl').write_text(json.dumps(instance.to_dict()) + '\n')
    suite = destination / 'tests/suite'
    runtime = suite / 'benchmarks/aacr'
    runtime.mkdir(parents=True)
    for name in ('__init__.py', 'score.py', 'transport.py'):
        shutil.copy2(PACKAGE / name, runtime / name)
    shutil.copytree(PACKAGE / 'upstream', runtime / 'upstream', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    pi = suite / 'benchmarks/pi'
    pi.mkdir()
    for name in ('pi_judge.mjs', 'package.json', 'package-lock.json'):
        shutil.copy2(PACKAGE.parent / 'pi' / name, pi / name)
    shutil.copy2(PACKAGE.parent / '__init__.py', suite / 'benchmarks/__init__.py')
    for name in ('pyproject.toml', 'README.md', 'LICENSE'):
        shutil.copy2(PACKAGE.parents[1] / name, suite / name)
    return instance


def prepare_context(instance: schema.ReviewInstance, task: Path) -> Path:
    """Fetch pinned trees and enough history into a fresh isolated repository."""
    environment = Path(task) / 'environment'
    environment.mkdir(parents=True, exist_ok=True)
    repository = environment / 'repo'
    if repository.exists():
        raise FileExistsError(f'Refusing to replace repository context: {repository}')
    with tempfile.TemporaryDirectory(prefix='.aacr-context-', dir=environment) as temporary:
        prepared = Path(temporary) / 'repo'
        def git(*arguments: str) -> subprocess.CompletedProcess:
            result = subprocess.run(['git', '-C', str(prepared), *arguments], text=True, capture_output=True,
                                    timeout=1800)
            if result.returncode:
                raise repo_utils.RepoError(f'Git context unavailable ({" ".join(arguments)}): {result.stderr.strip()}')
            return result
        prepared.mkdir()
        git('init', '-q')
        # Configure LFS for this repository only, not from host configuration.
        git('lfs', 'install', '--local', '--skip-repo')
        git('remote', 'add', 'origin', instance.resolved_clone_url)
        # Fetch both pinned commits and only as much history as their merge base needs.
        revisions = (instance.base_commit, instance.head_commit)
        git('fetch', '--no-tags', '--depth=32', 'origin', *revisions)
        depth = 32
        while True:
            common = subprocess.run(['git', '-C', str(prepared), 'merge-base', *revisions],
                                    text=True, capture_output=True, timeout=1800)
            shallow_path = prepared / '.git/shallow'
            boundaries = set(shallow_path.read_text().splitlines()) if shallow_path.exists() else set()
            if common.returncode == 0:
                # A shallow boundary between this merge base and either commit
                # could hide a nearer one, so deepen until there is none.
                exclusive = set(git('rev-list', *revisions, '^' + common.stdout.strip()).stdout.splitlines())
                if not boundaries.intersection(exclusive):
                    break
            if not boundaries:
                raise repo_utils.RepoError('Pinned base/head have no common history')
            git('fetch', '--no-tags', f'--deepen={depth}', 'origin', *revisions)
            depth *= 2
        git('checkout', '--detach', instance.head_commit)
        for revision in revisions:
            git('cat-file', '-e', revision + '^{commit}')
        if git('rev-parse', 'HEAD').stdout.strip() != instance.head_commit or git('status', '--porcelain').stdout:
            raise ValueError('Repository preparation did not produce a clean pinned head')
        prepared.rename(repository)
    return repository


def configure_daydream_task(task: Path, build_file: Path | None = None) -> None:
    """Make a rendered task run the pinned Daydream reviewer, or a candidate build."""
    from daydream import git_ops
    from daydream.deep.artifacts import diff_key

    suffix, assets, identity = build.reviewer_image(build_file)
    verifier_suffix, verifier_assets, _ = build.reviewer_image(build_file, entrypoint=False)
    environment = task / 'environment'
    metadata = tomllib.loads((task / 'task.toml').read_text())['metadata']
    repo = environment / 'repo'
    base, head = metadata['base_commit'], metadata['head_commit']
    if git_ops.head_sha(repo) != head or git_ops.status_porcelain(repo).strip():
        raise ValueError('Daydream task requires a clean pinned head')
    # Host system attributes (Apple Git's `*.mm diff=objc`) change hunk headers.
    with mock.patch.dict(os.environ, GIT_ATTR_NOSYSTEM='1'):
        patch = git_ops.diff(repo, base)
    comparison = dict(base_sha=base, head_sha=head,
        merge_base_sha=git_ops.merge_base(repo, base, head), diff_key=diff_key(patch),
        patch_sha256=hashlib.sha256(patch.encode()).hexdigest())
    case = environment / 'daydream-case'
    case.mkdir()
    (case / 'case.json').write_text(json.dumps(dict(format='aacr', repository='/workspace/repo',
        findings='/logs/artifacts/findings.json'), indent=2) + '\n')
    (case / 'comparison.json').write_text(json.dumps(comparison, indent=2) + '\n')
    (case / 'diff.patch').write_text(patch)
    (case / 'build.json').write_text(json.dumps(identity, indent=2) + '\n')
    shutil.copy2(case / 'comparison.json', task / 'tests/comparison.json')
    shutil.copy2(case / 'build.json', task / 'tests/build.json')
    # Reference annotations and the judge stay in the verifier's assets only.
    build.write_assets(environment, assets)
    build.write_assets(task / 'tests', verifier_assets)
    (environment / 'Dockerfile').write_text(
        f'FROM {build.NODE_IMAGE}\n'
        'RUN apt-get update && apt-get install -y --no-install-recommends git git-lfs ca-certificates ripgrep '
        '&& rm -rf /var/lib/apt/lists/*\n'
        'RUN git lfs install --system\n' + suffix +
        f'COPY repo/ /workspace/repo/\nCOPY daydream-case/ {build.CASE_DIR}/\n'
        'WORKDIR /workspace/repo\n')
    (environment / 'docker-compose.yaml').write_text(build.codex_login_compose())
    (task / 'tests/Dockerfile').write_text(
        f'FROM {build.NODE_IMAGE}\nRUN apt-get update && apt-get install -y --no-install-recommends '
        'git ca-certificates && rm -rf /var/lib/apt/lists/*\n' + verifier_suffix +
        'COPY . /tests/\nRUN uv pip install --no-deps --python /opt/daydream/bin/python /tests/suite '
        '&& npm ci --omit=dev --prefix /tests/suite/benchmarks/pi\nWORKDIR /tests/suite\n')
    script = (task / 'tests/test.sh').read_text().replace(
        '--output /logs/verifier', '--output /logs/verifier \\\n  --daydream-proof /logs/artifacts/daydream \\\n'
        f'  --comparison /tests/comparison.json --host-receipt {HOST_RECEIPT}')
    (task / 'tests/test.sh').write_text(script)
    config = (task / 'task.toml').read_text().replace('timeout_sec = 60.0', 'timeout_sec = 5400.0')
    config = config.replace('network_mode = "no-network"', 'network_mode = "public"')
    config = config.replace('cpus = 1', 'cpus = 4').replace('memory_mb = 2048', 'memory_mb = 8192')
    config = config.replace('fixed Oracle reviewer', 'complete Daydream reviewer').replace('"oracle"', '"daydream"')
    config = config.replace('review case and subscription judge',
                            'review case with the complete Daydream reviewer and subscription judge')
    config = config.replace('["code-review", "aacr"]', '["code-review", "aacr", "daydream"]')
    config += build.task_metadata(identity)
    (task / 'task.toml').write_text(config)
    if (task / 'solution').exists():
        shutil.rmtree(task / 'solution')


def load_records(upstream: Path) -> list[dict]:
    """Admit only the clean pinned source and checksum, validating every identity."""
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
    records = json.loads(dataset.read_text())
    if not isinstance(records, list) or not records:
        raise ValueError('Expected a nonempty AACR positive dataset array')
    names, identities = set(), set()
    for record in records:
        name = task_name(record)
        instance = validated_instance(record)
        if name in names:
            raise ValueError(f'duplicate task identity: {name}')
        if instance.instance_id in identities:
            raise ValueError(f'duplicate instance identity: {instance.instance_id}')
        names.add(name)
        identities.add(instance.instance_id)
    return sorted(records, key=task_name)


def write_task(record: dict, output: Path, daydream: bool, build_file: Path | None = None) -> Path:
    destination = output / task_name(record)
    if destination.exists():
        raise FileExistsError(f'Refusing to replace generated task: {destination}')
    with tempfile.TemporaryDirectory(prefix='.aacr-task-', dir=output) as temporary:
        stage = Path(temporary) / destination.name
        instance = render_task(record, stage)
        prepare_context(instance, stage)
        if daydream:
            configure_daydream_task(stage, build_file)
        stage.rename(destination)
    return destination


def generate_task(upstream: Path, output: Path, *, daydream: bool = False) -> Path:
    """Generate the Laravel oracle task run by experiments/aacr-oracle.yaml."""
    records = [record for record in load_records(upstream) if record['githubPrUrl'] == PR_URL]
    if len(records) != 1 or records[0]['source_commit'] != BASE or records[0]['target_commit'] != HEAD:
        raise ValueError('Expected exactly the pinned Laravel review case')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    return write_task(records[0], output, daydream)


def generate_catalog(upstream: Path, output: Path, *, daydream: bool = False, split: str = 'full',
                     task_names: list[str] | None = None, build_file: Path | None = None) -> list[Path]:
    """Generate every selected case and retain explicit revision/conversion failures."""
    records = load_records(upstream)
    total = len(records)
    if split != 'full':
        held_out = {line for line in HOLDOUT.read_text().splitlines() if not line.startswith('#')}
        # A stale name would quietly move its case into dev.
        if stale := held_out - {task_name(record) for record in records}:
            raise ValueError(f'holdout.txt lists unknown AACR cases: {sorted(stale)}')
        records = [record for record in records if (task_name(record) in held_out) == (split == 'holdout')]
    if task_names is not None:
        unknown = set(task_names) - {task_name(record) for record in records}
        if unknown:
            raise ValueError(f'Unknown AACR task names in the {split} set: {sorted(unknown)}')
        records = [record for record in records if task_name(record) in task_names]
        if not records:
            raise ValueError('Select at least one AACR task')
    output = Path(output).resolve()
    report_path = output / 'catalog.json'
    for destination in [report_path, *(output / task_name(record) for record in records)]:
        if destination.exists():
            raise FileExistsError(f'Refusing to replace generated catalog assets: {destination}')
    output.mkdir(parents=True, exist_ok=True)
    report = dict(aacr_revision=AACR_REVISION, dataset_sha256=DATASET_SHA256, split=split,
                  total_records=total, selected_records=len(records), generated_tasks=0,
                  reference_findings=0, errors=0, cases=[])
    tasks = []
    for record in records:
        instance = validated_instance(record)
        case = dict(task_name=task_name(record), instance_id=instance.instance_id,
                    pr_url=record['githubPrUrl'], language=record['project_main_language'],
                    base_commit=instance.base_commit, head_commit=instance.head_commit,
                    reference_findings=len(instance.reference_comments))
        report['reference_findings'] += case['reference_findings']
        try:
            tasks.append(write_task(record, output, daydream, build_file))
        except Exception as error:
            case.update(status='error', error=f'{type(error).__name__}: {error}')
            report['errors'] += 1
        else:
            case['status'] = 'generated'
            report['generated_tasks'] += 1
        report['cases'].append(case)
        report_path.write_text(json.dumps(report, indent=2) + '\n')
        print(f"{case['task_name']}: {case['status']}", flush=True)
    if report['errors']:
        raise ValueError(f"{report['errors']} of {len(records)} AACR cases failed; see {report_path}")
    return tasks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', required=True, type=Path, help='Clean checkout of the pinned Alibaba AACR source')
    parser.add_argument('--output', type=Path, default=Path('artifacts/aacr/tasks'))
    parser.add_argument('--split', choices=('full', 'dev', 'holdout'), default='full',
                        help='All 196 cases, the dev cases for tuning, or the held-out cases in holdout.txt')
    parser.add_argument('--task', action='append', dest='task_names',
                        help='Exact case to convert from the split; repeat to select several')
    parser.add_argument('--oracle', action='store_true', help='Generate only the pinned Laravel Oracle fixture')
    parser.add_argument('--build', type=Path, dest='build_file',
                        help="A candidate's build.json from benchmarks.daydream.build --candidate; omit for the pin")
    arguments = parser.parse_args()
    if arguments.oracle:
        if arguments.task_names or arguments.build_file or arguments.split != 'full':
            parser.error('--oracle cannot be combined with --task, --build or --split')
        print(generate_task(arguments.upstream, arguments.output))
    else:
        build.built_wheel(arguments.build_file)  # fail before converting anything
        generate_catalog(arguments.upstream, arguments.output, daydream=True, split=arguments.split,
                         task_names=arguments.task_names, build_file=arguments.build_file)
        print(arguments.output / 'catalog.json')


if __name__ == '__main__':
    main()
