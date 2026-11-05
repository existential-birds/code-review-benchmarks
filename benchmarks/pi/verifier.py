"""Hand the judge only a host-refreshed copy of its Pi login.

Pi coordinates refreshes through an ``auth.json.lock`` directory beside
``auth.json``, which only works when every process shares that folder. So the
host refreshes under Pi's own lock, and the judge's container gets a copy that
holds only the ``openai-codex`` access token, with enough validity left that it
never refreshes. The copy has no refresh token: a refresh there would rotate the
token without saving the new one to the host, which would end the host login, so
it fails instead.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from harbor.verifier.verifier import Verifier

PACKAGE = Path(__file__).resolve().parent
PROVIDER = 'openai-codex'
MODEL = 'gpt-6.1-sol'
JUDGE_AUTH_DIR = '/run/judge-pi'
# Above AACR's 300-second verifier timeout plus Pi's 5-minute refresh threshold,
# with room for a --verifier-timeout-multiplier up to about 4. Benchmarks with a
# longer verifier timeout raise it in their experiment configuration.
DEFAULT_MIN_VALIDITY_SEC = 30 * 60


class JudgeLoginError(RuntimeError):
    """The host Pi login can't be refreshed or handed to the judge; no credential details."""


async def refresh_host_login(auth_path: Path, min_validity_ms: int) -> None:
    """Refresh with the pinned Pi SDK under Pi's lock, only when validity is short."""
    # Once a refresh starts, killing it can lose the rotated token. Its own session
    # keeps Ctrl+C away from it, and a thread (unlike an asyncio child) isn't
    # killed when Harbor's timeout cancels this or its event loop closes.
    process = await asyncio.shield(asyncio.to_thread(subprocess.run,
        ['node', str(PACKAGE / 'pi_refresh.mjs'), str(auth_path), str(min_validity_ms)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        start_new_session=True))
    try:
        result = json.loads(process.stdout)
    except ValueError:
        result = {}
    if process.returncode != 0 or result.get('ok') is not True:
        category = (result.get('error') or {}).get('category', 'runtime')
        raise JudgeLoginError(f'Host Pi login refresh failed ({category})')


def read_login(auth_path: Path):
    """Read the openai-codex entry, waiting out another process's in-place rewrite."""
    deadline = time.monotonic() + 1
    while True:
        try:
            data = json.loads(auth_path.read_text(encoding='utf-8'))
            return data.get(PROVIDER) if isinstance(data, dict) else None
        except (OSError, ValueError):
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.01)


class PiJudgeVerifier(Verifier):
    """Harbor's verifier, after uploading a fresh copy of the judge's login."""

    def __init__(self, *args, min_judge_validity_sec: int = DEFAULT_MIN_VALIDITY_SEC, **kwargs):
        super().__init__(*args, **kwargs)
        self.min_judge_validity_sec = int(min_judge_validity_sec)
        if self.min_judge_validity_sec <= 0:
            raise ValueError('min_judge_validity_sec must be a positive number of seconds')

    async def upload_judge_login(self) -> None:
        directory = os.environ.get('JUDGE_PI_AUTH_DIR')
        if not directory:
            raise JudgeLoginError('Set JUDGE_PI_AUTH_DIR to your Pi login folder (usually ~/.pi/agent)')
        # Keep Pi's own path (not resolved) so the lock sits where Pi itself puts it.
        auth_path = Path(directory) / 'auth.json'
        if not auth_path.is_file():
            # Pi would otherwise create an empty login here.
            raise JudgeLoginError('No Pi login found at $JUDGE_PI_AUTH_DIR/auth.json')
        min_validity_ms = self.min_judge_validity_sec * 1000
        await refresh_host_login(auth_path, min_validity_ms)
        credential = read_login(auth_path)
        if (not isinstance(credential, dict) or credential.get('type') != 'oauth'
                or type(credential.get('expires')) not in (int, float)):
            raise JudgeLoginError('Pi has no openai-codex subscription login')
        if credential['expires'] - time.time() * 1000 < min_validity_ms:
            raise JudgeLoginError('Pi openai-codex login has too little validity left to score')
        # No -p: the folder already existing means a task generated before the
        # mount was removed still mounts the host login there, and the upload
        # would overwrite it.
        result = await self.environment.exec(f'mkdir -m 700 {JUDGE_AUTH_DIR}', user='root')
        if result.return_code != 0:
            raise JudgeLoginError(f'The judge container already has {JUDGE_AUTH_DIR}; '
                                  'regenerate tasks made before the login mount was removed')
        descriptor, temporary = tempfile.mkstemp(prefix='judge-pi-', suffix='.json')
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                json.dump({PROVIDER: dict(credential, refresh='')}, handle)
            await self.environment.upload_file(temporary, f'{JUDGE_AUTH_DIR}/auth.json')
        finally:
            os.unlink(temporary)

    async def verify(self):
        await self.upload_judge_login()
        return await super().verify()
