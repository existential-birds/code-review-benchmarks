"""The judge gets only a host-refreshed copy of its Pi login, or scoring never starts."""
import asyncio
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
from types import SimpleNamespace

import pytest

from benchmarks.pi import verifier as judge_verifier
from harbor.verifier.verifier import Verifier

HOUR_MS = 3600 * 1000


def login(expires_ms, **extra):
    return {'openai-codex': {'type': 'oauth', 'access': 'old-access', 'refresh': 'old-refresh',
                             'accountId': 'account', 'expires': expires_ms}, **extra}


class Environment:
    def __init__(self):
        self.calls = []
        self.uploads = {}
        self.login_folder_exists = False

    async def exec(self, command, **kwargs):
        self.calls.append(('exec', command, kwargs.get('user')))
        failed = command.startswith('mkdir') and self.login_folder_exists
        return SimpleNamespace(return_code=1 if failed else 0, stdout='', stderr='')

    async def upload_file(self, source_path, target_path):
        source = Path(source_path)
        self.calls.append(('upload', target_path))
        self.uploads[target_path] = (json.loads(source.read_text()), source.stat().st_mode & 0o777, source)


@pytest.fixture
def judge(tmp_path, monkeypatch):
    """A verifier over a real host login folder, recording when Harbor's own verification runs."""
    home = tmp_path / 'pi-agent'
    home.mkdir()
    (home / 'auth.json').write_text(json.dumps(login(int(time.time() * 1000) - 1000,
        anthropic={'type': 'oauth', 'access': 'other-provider', 'refresh': 'r', 'expires': 1})))
    monkeypatch.setenv('JUDGE_PI_AUTH_DIR', str(home))
    scratch = tmp_path / 'host-tmp'
    scratch.mkdir()
    monkeypatch.setattr(tempfile, 'tempdir', str(scratch))
    environment = Environment()
    async def harbor_verify(self):
        environment.calls.append(('harbor-verify',))
        return 'scored'
    monkeypatch.setattr(Verifier, 'verify', harbor_verify)
    refreshes = []
    async def refresh(auth_path, min_validity_ms):
        # Stands in for Pi's lock-guarded refresh, which rewrites the host file in place.
        refreshes.append((auth_path, min_validity_ms))
        data = json.loads(auth_path.read_text())
        data['openai-codex'].update(access='new-access', refresh='new-refresh',
                                    expires=int(time.time() * 1000) + 10 * HOUR_MS)
        auth_path.write_text(json.dumps(data))
    monkeypatch.setattr(judge_verifier, 'refresh_host_login', refresh)
    verifier = judge_verifier.PiJudgeVerifier(task=None, trial_paths=None, environment=environment)
    return SimpleNamespace(home=home, environment=environment, refreshes=refreshes, verifier=verifier,
                           scratch=scratch)


@pytest.mark.parametrize('kwargs,validity_ms', [({}, 30 * 60 * 1000),
                                               ({'min_judge_validity_sec': '2400'}, 40 * 60 * 1000)])
def test_uploads_only_the_refreshed_openai_codex_access_before_scoring(judge, kwargs, validity_ms):
    verifier = judge_verifier.PiJudgeVerifier(task=None, trial_paths=None, environment=judge.environment, **kwargs)
    assert asyncio.run(verifier.verify()) == 'scored'
    assert judge.refreshes == [(judge.home / 'auth.json', validity_ms)]
    assert judge.environment.calls == [('exec', 'mkdir -m 700 /run/judge-pi', 'root'),
                                       ('upload', '/run/judge-pi/auth.json'), ('harbor-verify',)]
    uploaded, mode, source = judge.environment.uploads['/run/judge-pi/auth.json']
    host = json.loads((judge.home / 'auth.json').read_text())['openai-codex']
    assert host['refresh'] == 'new-refresh'
    # Without a refresh token the container can't rotate the host login.
    assert uploaded == {'openai-codex': dict(host, refresh='')}
    assert mode == 0o600
    assert source.parent == judge.scratch
    assert list(judge.scratch.iterdir()) == []


@pytest.mark.parametrize('failure', ['refresh-fails', 'still-short', 'api-key', 'no-variable', 'no-login',
                                     'old-task-mount'])
def test_any_login_failure_stops_the_trial_before_scoring(judge, monkeypatch, failure):
    before = (judge.home / 'auth.json').read_text()
    if failure == 'refresh-fails':
        async def refresh(auth_path, min_validity_ms):
            raise judge_verifier.JudgeLoginError('Host Pi login refresh failed (authentication)')
        monkeypatch.setattr(judge_verifier, 'refresh_host_login', refresh)
    elif failure == 'still-short':
        async def refresh(auth_path, min_validity_ms):
            auth_path.write_text(json.dumps(login(int(time.time() * 1000) + 10 * 60 * 1000)))
        monkeypatch.setattr(judge_verifier, 'refresh_host_login', refresh)
    elif failure == 'api-key':
        (judge.home / 'auth.json').write_text(json.dumps({'openai-codex': {'type': 'api_key', 'key': 'k'}}))
        async def refresh(auth_path, min_validity_ms):
            pass
        monkeypatch.setattr(judge_verifier, 'refresh_host_login', refresh)
    elif failure == 'no-variable':
        monkeypatch.delenv('JUDGE_PI_AUTH_DIR')
    elif failure == 'old-task-mount':
        # An old task's compose file mounts the host login at /run/judge-pi.
        judge.environment.login_folder_exists = True
    else:
        missing = judge.home.parent / 'missing'
        monkeypatch.setenv('JUDGE_PI_AUTH_DIR', str(missing))
    with pytest.raises(judge_verifier.JudgeLoginError):
        asyncio.run(judge.verifier.verify())
    assert judge.environment.calls == ([('exec', 'mkdir -m 700 /run/judge-pi', 'root')]
                                       if failure == 'old-task-mount' else [])
    assert judge.environment.uploads == {}
    assert list(judge.scratch.iterdir()) == []
    if failure in ('no-variable', 'no-login'):
        assert judge.refreshes == []
        assert (judge.home / 'auth.json').read_text() == before
        assert not (judge.home.parent / 'missing').exists()


@pytest.mark.skipif(shutil.which('node') is None
                    or not (judge_verifier.PACKAGE / 'node_modules/@earendil-works/pi-coding-agent').is_dir(),
                    reason='host refresh needs Node and the pinned Pi SDK in benchmarks/pi/node_modules')
def test_pinned_sdk_leaves_a_fresh_login_untouched_and_rejects_a_missing_one(tmp_path):
    auth_path = tmp_path / 'auth.json'
    auth_path.write_text(json.dumps(login(int(time.time() * 1000) + 10 * HOUR_MS)))
    before = auth_path.stat()
    asyncio.run(judge_verifier.refresh_host_login(auth_path, 30 * 60 * 1000))
    after = auth_path.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    assert sorted(path.name for path in tmp_path.iterdir()) == ['auth.json']
    auth_path.write_text(json.dumps({'anthropic': {'type': 'api_key', 'key': 'k'}}))
    with pytest.raises(judge_verifier.JudgeLoginError, match=r'\(authentication\)'):
        asyncio.run(judge_verifier.refresh_host_login(auth_path, 30 * 60 * 1000))


def test_waits_out_another_process_rewriting_the_login(tmp_path):
    # Pi rewrites auth.json in place (truncate, then write), so a read can land in between.
    auth_path = tmp_path / 'auth.json'
    auth_path.write_text('{"openai-co')
    writer = threading.Timer(0.1, auth_path.write_text, [json.dumps(login(5))])
    writer.start()
    try:
        assert judge_verifier.read_login(auth_path) == login(5)['openai-codex']
    finally:
        writer.join()
