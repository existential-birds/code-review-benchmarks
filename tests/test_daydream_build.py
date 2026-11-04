"""The one Daydream pin: building it, and refusing any other wheel."""
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.daydream import build


def test_converters_get_only_the_pinned_wheel(pinned_wheel):
    wheel, lock = build.pinned_wheel()
    assert wheel == pinned_wheel and lock.name == build.LOCK_NAME
    _, assets, identity = build.reviewer_image()
    assert identity['reviewer_runtime_lock_sha256'] == hashlib.sha256(lock.read_bytes()).hexdigest()
    assert assets[wheel.name] == wheel.read_bytes()
    pinned_wheel.write_bytes(pinned_wheel.read_bytes() + b'changed wheel bytes')
    with pytest.raises(ValueError, match='Daydream wheel SHA-256 differs from benchmarks/daydream/build.json'):
        build.pinned_wheel()
    pinned_wheel.unlink()
    with pytest.raises(FileNotFoundError, match='Build the pinned Daydream first'):
        build.reviewer_image()


@pytest.mark.parametrize('pinned', [True, False])
def test_build_replaces_the_cache_only_with_the_pinned_wheel(tmp_path, monkeypatch, pinned):
    """Git archive and uv stand in for a Daydream clone; the hash check is the suite's own."""
    built = b'wheel bytes from the pinned commit'
    monkeypatch.setitem(build.BUILD, 'wheel_sha256', hashlib.sha256(built).hexdigest())
    commands = []
    def run(command, cwd=None, **_):
        commands.append(command[:2])
        if command[:2] == ['uv', 'build']:
            output = Path(command[command.index('--out-dir') + 1])
            output.mkdir()
            (output / build.BUILD['wheel_name']).write_bytes(built if pinned else b'other bytes')
        return SimpleNamespace(stdout=b'locked==1.0\n')
    monkeypatch.setattr(build, 'subprocess', SimpleNamespace(run=run))
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
    assert commands[0] == ['git', '-C']
