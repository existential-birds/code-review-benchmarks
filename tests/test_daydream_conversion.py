"""Generated Daydream task boundaries, using real task assets and a faked Git checkout."""
import hashlib
import json
import tomllib
import zipfile

import pytest
import yaml

from benchmarks.aacr import convert
from fake_git import BASE, HEAD, PATCH, FakeCheckout


@pytest.fixture
def pinned_wheel(tmp_path, monkeypatch):
    wheel = tmp_path / 'daydream-0.30.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('daydream-0.30.0.dist-info/METADATA', 'Name: daydream\nVersion: 0.30.0\n')
        archive.writestr('daydream/benchmark/reviewbench/harbor_entrypoint.py', '')
    monkeypatch.setattr(convert, 'DAYDREAM_BUILD', dict(convert.DAYDREAM_BUILD,
        wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest()))
    return wheel


def daydream_case(tmp_path, monkeypatch, pr_url=convert.PR_URL, comments=()):
    """A rendered review case whose checkout Git would report at BASE..HEAD."""
    task = tmp_path / 'task'
    convert.render_task({'githubPrUrl': pr_url, 'project_main_language': 'PHP',
        'source_commit': convert.BASE, 'target_commit': convert.HEAD, 'comments': list(comments)}, task)
    repo = task / 'environment/repo'
    repo.mkdir()
    (repo / 'file.php').write_text('head\n')
    metadata = (task / 'task.toml').read_text().replace(convert.BASE, BASE).replace(convert.HEAD, HEAD)
    (task / 'task.toml').write_text(metadata)
    return task, FakeCheckout(repo).install(monkeypatch)


@pytest.mark.parametrize('pr_url', [convert.PR_URL, 'https://github.com/owner/project/pull/17'])
def test_daydream_task_freezes_runtime_comparison_and_keeps_oracle_separate(tmp_path, monkeypatch, pinned_wheel, pr_url):
    task, checkout = daydream_case(tmp_path, monkeypatch, pr_url,
                                   [{'path': 'file.php', 'note': 'verifier only annotation'}])
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
    assert patch == PATCH.encode()
    assert comparison == dict(base_sha=BASE, head_sha=HEAD, merge_base_sha=BASE,
        patch_sha256=hashlib.sha256(patch).hexdigest(), diff_key=hashlib.sha256(patch).hexdigest())
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


@pytest.mark.parametrize('pr_url', [convert.PR_URL, 'https://github.com/owner/project/pull/17'])
def test_daydream_task_refuses_a_checkout_that_is_not_the_clean_pinned_head(tmp_path, monkeypatch, pinned_wheel, pr_url):
    task, checkout = daydream_case(tmp_path, monkeypatch, pr_url)
    for change in ('head', 'dirty'):
        if change == 'head':
            checkout.head = BASE
        else:
            checkout.head = HEAD
            (task / 'environment/repo/file.php').write_text('operator edit\n')
        with pytest.raises(ValueError, match='clean pinned head'):
            convert.configure_daydream_task(task, pinned_wheel)
        assert not (task / 'environment/aacr').exists()


@pytest.mark.parametrize('reviewer', ['plain', 'daydream'])
def test_review_image_materializes_lfs_before_copying_the_checkout(tmp_path, monkeypatch, pinned_wheel, reviewer):
    # The host checkout holds real LFS objects; the image needs LFS filters before
    # the copy, or Git in the container reports every LFS file as modified.
    task, _ = daydream_case(tmp_path, monkeypatch, 'https://github.com/owner/project/pull/17')
    if reviewer == 'daydream':
        convert.configure_daydream_task(task, pinned_wheel)
    lines = (task / 'environment/Dockerfile').read_text().splitlines()
    install = next(index for index, line in enumerate(lines) if 'apt-get install' in line)
    assert 'git-lfs' in lines[install]
    lfs = lines.index('RUN git lfs install --system')
    copy = next(index for index, line in enumerate(lines) if line.startswith('COPY repo/'))
    assert install < lfs < copy


def test_frozen_patch_ignores_host_system_git_attributes(tmp_path, pinned_wheel, monkeypatch):
    """Apple Git's system attributes change `.mm` hunk headers; the reviewer's Git has none."""
    monkeypatch.delenv('GIT_ATTR_NOSYSTEM', raising=False)
    task, checkout = daydream_case(tmp_path, monkeypatch)
    convert.configure_daydream_task(task, pinned_wheel)
    assert [environment.get('GIT_ATTR_NOSYSTEM') for environment in checkout.diff_environments] == ['1']
