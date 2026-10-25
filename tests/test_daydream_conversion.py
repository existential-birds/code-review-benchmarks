"""Generated Daydream task boundaries, using real task and Git assets."""
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib
import zipfile

import pytest
import yaml

from benchmarks.aacr import convert


def test_daydream_task_freezes_runtime_comparison_and_keeps_oracle_separate(tmp_path, monkeypatch):
    record = {
        'githubPrUrl': convert.PR_URL, 'project_main_language': 'PHP',
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
    wheel = tmp_path / 'daydream-0.30.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('daydream-0.30.0.dist-info/METADATA', 'Name: daydream\nVersion: 0.30.0\n')
        archive.writestr('daydream/benchmark/reviewbench/harbor_entrypoint.py', '')
    monkeypatch.setattr(convert, 'DAYDREAM_BUILD', dict(convert.DAYDREAM_BUILD,
        wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest()))
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
    assert config['artifacts'] == ['/logs/agent/host-receipt.json']
    assert config['metadata']['daydream_wheel_sha256'] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert '--daydream-proof /logs/artifacts/daydream' in (task / 'tests/test.sh').read_text()
    assert '--host-receipt /logs/agent/host-receipt.json' in (task / 'tests/test.sh').read_text()
    assert not (task / 'solution').exists()
    assert 'verifier only annotation' not in ''.join(p.read_text() for p in (task / 'environment/aacr').glob('*.json'))
    assert 'oracle' in (convert.PACKAGE / 'templates/task.toml').read_text()
