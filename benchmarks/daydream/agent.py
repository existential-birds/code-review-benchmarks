"""Supported Harbor installed-agent seam for the Daydream reviewer, in any benchmark."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import shlex
from typing import Annotated
import uuid

from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.installed.base import BaseInstalledAgent
from harbor.agents.options import Cli, InstalledAgentOptions

from benchmarks.daydream.build import BUILD, CASE_DIR, CODEX_LOGIN_DIR


class DaydreamOptions(InstalledAgentOptions):
    """Native Harbor option for retaining Daydream's local capture records."""

    capture_data: Annotated[bool, Cli('--capture-data')] = False


class DaydreamAgent(BaseInstalledAgent):
    """Use the baked native runtime; Harbor owns trial execution and storage."""

    capabilities = AgentCapabilities(atif=True)
    options_model = DaydreamOptions

    @staticmethod
    def name() -> str:
        return 'daydream'

    def get_version_command(self) -> str:
        return "python -I -c \"import importlib.metadata as m; print(m.version('daydream'))\""

    def _require_shared_logs(self, environment) -> None:
        if not environment.is_path_mounted('/logs/agent', self.logs_dir):
            raise RuntimeError('Daydream requires shared native agent logs; configure auto_mounting=true')

    async def install(self, environment) -> None:
        self._require_shared_logs(environment)
        result = await environment.exec('unshare -Ur true')
        if result.return_code != 0:
            raise RuntimeError('Native Codex namespace admission failed')
        result = await environment.exec(f'test "$(ls -A {CODEX_LOGIN_DIR})" = auth.json')
        if result.return_code != 0:
            raise RuntimeError('The reviewer must see only the Codex auth.json; '
                               'regenerate tasks made before the login mount changed')
        # A catalog keeps the Daydream it was generated with; refuse one that
        # predates the suite's current pin instead of measuring an old reviewer.
        result = await environment.exec(f'cat {CASE_DIR}/build.json')
        try:
            baked = json.loads(result.stdout or '')
        except ValueError:
            baked = {}
        if result.return_code != 0 or any(baked.get(key) != BUILD[key] for key in BUILD):
            raise RuntimeError(f'Task was generated for Daydream {baked.get("version")}; '
                               f'regenerate it for the pinned {BUILD["version"]}')
        probe = ('from pathlib import Path; from benchmarks.daydream.entrypoint import verify_build; '
                 f"verify_build(Path('{CASE_DIR}/build.json'))")
        result = await environment.exec('python -I -c ' + shlex.quote(probe))
        if result.return_code != 0:
            raise RuntimeError('Pinned Daydream runtime admission failed')

    async def run(self, instruction, environment, context) -> None:
        self._require_shared_logs(environment)
        if self.extra_env:
            raise ValueError('Daydream uses the baked runtime and dedicated credential mount')
        invocation = str(uuid.uuid4())
        receipt = {'invocation_id': invocation, 'status': 'failed', 'return_code': None}
        # Trial root is outside Harbor's reviewer-writable log/artifact mounts.
        receipt_path = self.logs_dir.parent / 'daydream-receipt.json'
        receipt_path.write_text(json.dumps(receipt) + '\n')
        request_path = self.logs_dir / 'request.json'
        request_path.write_text(json.dumps({'invocation_id': invocation}) + '\n')
        result = await environment.exec(
            'test ! -e /logs/artifacts/daydream/request.json '
            '&& mkdir -p /logs/artifacts/daydream')
        if result.return_code != 0:
            raise RuntimeError('Prior Daydream invocation artifacts exist')
        await environment.upload_file(request_path, '/logs/artifacts/daydream/request.json')
        try:
            await environment.upload_file(receipt_path, '/logs/artifacts/daydream/host-receipt.json')
            result = await environment.exec(
                f'python -I -m benchmarks.daydream.entrypoint --invocation {invocation} '
                + self.build_cli_flags()
                + ' > /logs/agent/daydream.stdout.log 2> /logs/agent/daydream.stderr.log')
            receipt['return_code'] = result.return_code
            if result.return_code != 0:
                raise RuntimeError('Daydream invocation failed; inspect retained execution evidence')
            await environment.download_file('/logs/artifacts/daydream/execution.json',
                                            self.logs_dir / 'execution.json')
            self.populate_context_post_run(context)
            receipt['status'] = 'succeeded'
            receipt_path.write_text(json.dumps(receipt) + '\n')
            # Publish success only after every other asynchronous finalization step.
            await environment.upload_file(receipt_path, '/logs/artifacts/daydream/host-receipt.json')
        except BaseException as error:
            receipt['status'] = 'cancelled' if isinstance(error, asyncio.CancelledError) else 'failed'
            # The separate verifier reads this host-only document even when the
            # interrupted reviewer transport cannot accept remote invalidation.
            receipt_path.write_text(json.dumps(receipt) + '\n')
            with suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(environment.upload_file(
                    receipt_path, '/logs/artifacts/daydream/host-receipt.json')), 10)
            with suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(environment.download_file(
                    '/logs/artifacts/daydream/execution.json', self.logs_dir / 'execution.json')), 10)
            self.populate_context_post_run(context)
            raise
        finally:
            context.metadata = {**(context.metadata or {}), 'invocation_id': invocation,
                                'invocation_status': receipt['status']}

    def populate_context_post_run(self, context) -> None:
        """Populate usage and metadata together before Harbor tests for emptiness."""
        try:
            execution = json.loads((self.logs_dir / 'execution.json').read_text())
        except (OSError, ValueError):
            return
        usage = execution.get('usage', {})
        for attribute, key in (('n_input_tokens', 'prompt_tokens'),
                               ('n_cache_tokens', 'cached_tokens'),
                               ('n_output_tokens', 'completion_tokens')):
            value = usage.get(key)
            if type(value) is int and value >= 0:
                setattr(context, attribute, value)
        context.metadata = {**(context.metadata or {}),
            'elapsed_seconds': execution.get('elapsed_seconds'),
            'review_status': execution.get('status'),
            'omitted_findings': execution.get('omitted_findings'),
            'build': execution.get('build'),
            'resolved_configuration': execution.get('resolved_configuration')}
