"""Supported Harbor installed-agent seam for the AACR Daydream reviewer."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import json
from pathlib import Path
import uuid

from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.installed.base import BaseInstalledAgent
from harbor.agents.options import InstalledAgentOptions


class DaydreamOptions(InstalledAgentOptions):
    """Native Harbor option for retaining Daydream's local capture records."""

    capture_data: bool = False


class DaydreamAgent(BaseInstalledAgent):
    """Use the baked native runtime; Harbor owns trial execution and storage."""

    capabilities = AgentCapabilities(atif=True)
    options_model = DaydreamOptions

    @staticmethod
    def name() -> str:
        return 'daydream-aacr'

    def _require_shared_logs(self, environment) -> None:
        if not environment.is_path_mounted('/logs/agent', self.logs_dir):
            raise RuntimeError('Daydream requires shared native agent logs; configure auto_mounting=true')

    async def install(self, environment) -> None:
        self._require_shared_logs(environment)
        result = await environment.exec('unshare -Ur true')
        if result.return_code != 0:
            raise RuntimeError('Native Codex namespace admission failed')
        result = await environment.exec(
            "python -I -c \"from pathlib import Path; "
            "from benchmarks.aacr.daydream_entrypoint import verify_build; "
            "verify_build(Path('/opt/aacr/build.json'))\"")
        if result.return_code != 0:
            raise RuntimeError('Pinned Daydream runtime admission failed')

    async def run(self, instruction, environment, context) -> None:
        self._require_shared_logs(environment)
        if self.extra_env:
            raise ValueError('AACR baseline configuration uses the baked runtime and dedicated credential mount')
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
                'python -I -m benchmarks.aacr.daydream_entrypoint --invocation ' + invocation
                + (' --capture-data' if self.options.capture_data else '')
                + ' > /logs/agent/daydream.stdout.log 2> /logs/agent/daydream.stderr.log',
                cwd='/workspace/repo')
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
            'build': execution.get('build'),
            'resolved_configuration': execution.get('resolved_configuration')}
