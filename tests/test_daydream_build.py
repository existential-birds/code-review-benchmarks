"""The one Daydream pin and candidate builds: building them, and refusing any other wheel."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.daydream import build


def test_converters_get_only_the_wheel_their_build_names(pinned_wheel, candidate_build):
    _, wheel, lock = build.built_wheel()
    assert wheel == pinned_wheel and lock.name == build.LOCK_NAME
    _, assets, identity = build.reviewer_image()
    assert identity['reviewer_runtime_lock_sha256'] == hashlib.sha256(lock.read_bytes()).hexdigest()
    assert assets[wheel.name] == wheel.read_bytes() and 'candidate' not in identity

    # A candidate's own wheel and lock, never the pin's, under its own identity.
    candidate = candidate_build.parent / json.loads(candidate_build.read_text())['wheel_name']
    lines, assets, identity = build.reviewer_image(candidate_build)
    assert identity['candidate'] is True and identity['source_commit'] == 'c' * 40
    assert assets[candidate.name] == candidate.read_bytes() and pinned_wheel.name not in assets
    assert assets['reviewer-runtime.lock'] == (candidate_build.parent / build.LOCK_NAME).read_bytes()
    assert f'/opt/{candidate.name}' in lines
    candidate.write_bytes(candidate.read_bytes() + b'changed wheel bytes')
    with pytest.raises(ValueError, match=f'Daydream wheel SHA-256 differs from {candidate_build}'):
        build.reviewer_image(candidate_build)

    pinned_wheel.write_bytes(pinned_wheel.read_bytes() + b'changed wheel bytes')
    with pytest.raises(ValueError, match='Daydream wheel SHA-256 differs from benchmarks/daydream/build.json'):
        build.built_wheel()
    pinned_wheel.unlink()
    with pytest.raises(FileNotFoundError, match='Build Daydream first'):
        build.reviewer_image()


def fake_tools(monkeypatch, wheel_name, built):
    """Git and uv as a Daydream clone would answer them; returns the commands run."""
    commands = []
    def run(command, cwd=None, text=False, **_):
        commands.append(command)
        if command[:2] == ['uv', 'build']:
            output = Path(command[command.index('--out-dir') + 1])
            output.mkdir()
            (output / wheel_name).write_bytes(built)
        if 'rev-parse' in command:
            return SimpleNamespace(stdout='d' * 40 + '\n')
        return SimpleNamespace(stdout='locked==1.0\n' if text else b'locked==1.0\n')
    monkeypatch.setattr(build, 'subprocess', SimpleNamespace(run=run))
    return commands


@pytest.mark.parametrize('pinned', [True, False])
def test_build_replaces_the_cache_only_with_the_pinned_wheel(tmp_path, monkeypatch, pinned):
    """The hash check is the suite's own."""
    built = b'wheel bytes from the pinned commit'
    monkeypatch.setitem(build.BUILD, 'wheel_sha256', hashlib.sha256(built).hexdigest())
    commands = fake_tools(monkeypatch, build.BUILD['wheel_name'], built if pinned else b'other bytes')
    cache = tmp_path / '.cache/daydream'
    cache.mkdir(parents=True)
    (cache / 'previous.whl').write_text('earlier build')
    if pinned:
        assert build.build(tmp_path / 'daydream', cache) == cache / build.BUILD['wheel_name']
        assert sorted(path.name for path in cache.iterdir()) == [build.BUILD['wheel_name'], build.LOCK_NAME]
        assert (cache / build.LOCK_NAME).read_bytes() == b'locked==1.0\n'
    else:
        with pytest.raises(ValueError, match='built wheel differs'):
            build.build(tmp_path / 'daydream', cache)
        assert [path.name for path in cache.iterdir()] == ['previous.whl']
    assert commands[0][:3] == ['git', '-C', str(tmp_path / 'daydream')]
    assert commands[0][3:] == ['archive', build.BUILD['source_commit']]


def test_candidate_builds_any_commit_beside_its_own_identity_and_leaves_the_pin(tmp_path, monkeypatch):
    cache = tmp_path / '.cache/daydream'
    cache.mkdir(parents=True)
    (cache / 'pinned.whl').write_text('the pinned build')
    monkeypatch.setattr(build, 'CACHE', cache)
    root, commit = tmp_path / '.cache/daydream-candidates', 'd' * 40
    for built in (b'first candidate bytes', b'rebuilt candidate bytes'):
        commands = fake_tools(monkeypatch, 'daydream-0.35.0.dev1-py3-none-any.whl', built)
        assert build.build_candidate(tmp_path / 'daydream', 'my-branch', root) == root / commit / 'build.json'
    # The branch is resolved once, so the build names a commit that can't move.
    assert commands[0][-1] == 'my-branch^{commit}'
    assert ['git', '-C', str(tmp_path / 'daydream'), 'archive', commit] in commands
    assert json.loads((root / commit / 'build.json').read_text()) == {
        'source_commit': commit, 'version': '0.35.0.dev1', 'wheel_name': 'daydream-0.35.0.dev1-py3-none-any.whl',
        'wheel_sha256': hashlib.sha256(b'rebuilt candidate bytes').hexdigest(),
        'codex_cli': build.BUILD['codex_cli'], 'candidate': True}
    assert sorted(path.name for path in root.iterdir()) == [commit]
    assert sorted(path.name for path in (root / commit).iterdir()) == [
        'build.json', 'daydream-0.35.0.dev1-py3-none-any.whl', build.LOCK_NAME]
    assert build.built_wheel(root / commit / 'build.json')[1].read_bytes() == b'rebuilt candidate bytes'
    assert [path.name for path in cache.iterdir()] == ['pinned.whl']
