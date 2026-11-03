"""Generated Daydream task boundaries, using real task and Git assets."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib
import zipfile

import pytest
import yaml

from benchmarks.aacr import convert


@pytest.fixture
def pinned_wheel(tmp_path, monkeypatch):
    wheel = tmp_path / 'daydream-0.30.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('daydream-0.30.0.dist-info/METADATA', 'Name: daydream\nVersion: 0.30.0\n')
        archive.writestr('daydream/benchmark/reviewbench/harbor_entrypoint.py', '')
    monkeypatch.setattr(convert, 'DAYDREAM_BUILD', dict(convert.DAYDREAM_BUILD,
        wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest()))
    return wheel


@pytest.mark.parametrize('pr_url', [convert.PR_URL, 'https://github.com/owner/project/pull/17'])
def test_daydream_task_freezes_runtime_comparison_and_keeps_oracle_separate(tmp_path, pinned_wheel, pr_url):
    record = {
        'githubPrUrl': pr_url, 'project_main_language': 'PHP',
        'source_commit': convert.BASE, 'target_commit': convert.HEAD,
        'comments': [{'path': 'file.php', 'note': 'verifier only annotation'}],
    }
    task = tmp_path / 'task'
    convert.render_task(record, task)
    repo = task / 'environment/repo'
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
    metadata = (task / 'task.toml').read_text().replace(convert.BASE, base).replace(convert.HEAD, head)
    (task / 'task.toml').write_text(metadata)
    wheel = pinned_wheel
    modified_wheel = tmp_path / 'modified' / wheel.name
    modified_wheel.parent.mkdir()
    modified_wheel.write_bytes(wheel.read_bytes() + b'changed wheel bytes')
    with pytest.raises(ValueError, match='Daydream wheel SHA-256 differs from the baseline build'):
        convert.configure_daydream_task(task, modified_wheel)
    assert not (task / 'environment/aacr').exists()
    renamed_wheel = wheel.with_name('daydream-0.30.1-py3-none-any.whl')
    renamed_wheel.write_bytes(wheel.read_bytes())
    with pytest.raises(ValueError, match='Daydream wheel filename differs from the baseline build'):
        convert.configure_daydream_task(task, renamed_wheel)
    assert not (task / 'environment/aacr').exists()
    convert.configure_daydream_task(task, wheel)
    comparison = json.loads((task / 'environment/aacr/comparison.json').read_text())
    patch = (task / 'environment/aacr/diff.patch').read_bytes()
    assert comparison == dict(base_sha=base, head_sha=head, merge_base_sha=base,
        patch_sha256=hashlib.sha256(patch).hexdigest(), diff_key=hashlib.sha256(patch).hexdigest())
    assert b'-base\n+head' in patch
    assert (task / 'tests/comparison.json').read_bytes() == (task / 'environment/aacr/comparison.json').read_bytes()
    config = tomllib.loads((task / 'task.toml').read_text())
    assert config['agent']['timeout_sec'] >= 1800
    assert config['environment']['network_mode'] == 'public'
    compose = yaml.safe_load((task / 'environment/docker-compose.yaml').read_text())
    assert compose['services']['main']['security_opt'] == ['seccomp=unconfined']
    # Only the Codex login file, writable, so an in-container refresh reaches the host.
    assert compose['services']['main']['volumes'] == [{'type': 'bind',
        'source': '${AACR_CODEX_AUTH_DIR:?set to your Codex login folder, usually ~/.codex}/auth.json',
        'target': '/run/aacr-codex/auth.json', 'bind': {'create_host_path': False}}]
    assert not (task / 'tests/docker-compose.yaml').exists()
    assert config.get('artifacts', []) == []
    assert config['metadata']['daydream_wheel_sha256'] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert '--daydream-proof /logs/artifacts/daydream' in (task / 'tests/test.sh').read_text()
    assert '--host-receipt /tmp/aacr-host-receipt.json' in (task / 'tests/test.sh').read_text()
    assert not (task / 'solution').exists()
    assert 'verifier only annotation' not in ''.join(p.read_text() for p in (task / 'environment/aacr').glob('*.json'))
    assert config['task']['keywords'] == ['code-review', 'aacr', 'daydream']


@pytest.mark.parametrize('reviewer', ['plain', 'daydream'])
def test_emitted_runtime_preserves_clean_materialized_lfs_context(tmp_path, monkeypatch, pinned_wheel, reviewer):
    # Host conversion has native LFS filters; Docker starts with neutral Git config.
    host_system = tmp_path / 'host-system.gitconfig'
    host_global = tmp_path / 'host-global.gitconfig'
    host_global.touch()
    monkeypatch.setenv('GIT_CONFIG_SYSTEM', str(host_system))
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(host_global))
    subprocess.run(['git', 'lfs', 'install', '--system'], cwd=tmp_path, check=True, capture_output=True)
    task = tmp_path / 'task'
    record = {'githubPrUrl': 'https://github.com/owner/project/pull/17',
              'project_main_language': 'Python', 'source_commit': convert.BASE,
              'target_commit': convert.HEAD, 'comments': []}
    convert.render_task(record, task)
    repo = task / 'environment/repo'
    repo.mkdir()
    def git(*args, directory=repo, env=None):
        return subprocess.check_output(['git', '-C', str(directory), *args], text=True, env=env).strip()
    git('init', '-q')
    (repo / 'file.py').write_text('base\n')
    git('add', '.')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'base')
    base = git('rev-parse', 'HEAD')
    git('lfs', 'track', '*.bin')
    contents = 'materialized LFS object\n' * 100
    (repo / 'asset.bin').write_text(contents)
    git('add', '.')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'LFS head')
    head = git('rev-parse', 'HEAD')
    assert git('show', 'HEAD:asset.bin').startswith('version https://git-lfs.github.com/spec/v1')
    assert git('status', '--porcelain') == ''
    metadata = (task / 'task.toml').read_text().replace(convert.BASE, base).replace(convert.HEAD, head)
    (task / 'task.toml').write_text(metadata)
    if reviewer == 'daydream':
        convert.configure_daydream_task(task, pinned_wheel)
    dockerfile = (task / 'environment/Dockerfile').read_text()
    runtime_system = tmp_path / 'runtime-system.gitconfig'
    runtime_global = tmp_path / 'runtime-global.gitconfig'
    runtime_system.touch()
    runtime_global.touch()
    runtime_env = dict(os.environ, GIT_CONFIG_SYSTEM=str(runtime_system), GIT_CONFIG_GLOBAL=str(runtime_global))
    copied = tmp_path / 'runtime-repo'
    shutil.copytree(repo, copied)
    assert (copied / 'asset.bin').read_text() == contents
    assert 'asset.bin' in git('status', '--porcelain', directory=copied, env=runtime_env)
    # Execute the emitted pre-COPY setup with the real native binary, using a
    # private system config file so the test cannot modify the operator's filters.
    for line in dockerfile.splitlines():
        if line.startswith('COPY repo/'):
            break
        if line.startswith('RUN git lfs '):
            subprocess.run(line[4:], shell=True, cwd=tmp_path, env=runtime_env,
                           check=True, capture_output=True)
    assert git('status', '--porcelain', directory=copied, env=runtime_env) == ''
    assert git('rev-parse', 'HEAD', directory=copied, env=runtime_env) == head
    assert (copied / 'asset.bin').read_text() == contents
    assert 'git-lfs' in next(line for line in dockerfile.splitlines() if 'apt-get install' in line)


def test_frozen_patch_ignores_host_system_git_attributes(tmp_path, pinned_wheel, monkeypatch):
    """Apple Git's system attributes change `.mm` hunk headers; the reviewer's Git has none."""
    from daydream import git_ops
    monkeypatch.delenv('GIT_ATTR_NOSYSTEM', raising=False)
    task = tmp_path / 'task'
    convert.render_task({'githubPrUrl': convert.PR_URL, 'project_main_language': 'PHP',
        'source_commit': convert.BASE, 'target_commit': convert.HEAD, 'comments': []}, task)
    repo = task / 'environment/repo'
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
    git('init', '-q')
    (repo / 'file.php').write_text('base\n')
    git('add', '.')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'base')
    commit = git('rev-parse', 'HEAD')
    metadata = (task / 'task.toml').read_text().replace(convert.BASE, commit).replace(convert.HEAD, commit)
    (task / 'task.toml').write_text(metadata)
    seen = []
    real_diff = git_ops.diff
    monkeypatch.setattr(git_ops, 'diff', lambda *args: seen.append(os.environ.get('GIT_ATTR_NOSYSTEM')) or real_diff(*args))
    convert.configure_daydream_task(task, pinned_wheel)
    assert seen == ['1']
