"""Bridge host-owned process completion into Harbor's isolated verifier."""
from __future__ import annotations

import json

from benchmarks.aacr.judge_verifier import PiJudgeVerifier


class DaydreamVerifier(PiJudgeVerifier):
    """Keep the native verifier workflow and authenticate its process receipt."""

    async def verify(self):
        # Reviewer logs include the verifier log mount. Discard a forged/stale
        # reward even when receipt admission fails before native verification.
        self.trial_paths.reward_json_path.unlink(missing_ok=True)
        self.trial_paths.reward_text_path.unlink(missing_ok=True)
        if (self.environment.environment_dir.resolve() != self.task.paths.tests_dir.resolve()
                or self.environment.is_path_mounted('/logs/agent', self.trial_paths.agent_dir)
                or self.environment.is_path_mounted('/logs/artifacts',
                    self.trial_paths.artifacts_dir / 'logs/artifacts')):
            raise RuntimeError('Daydream completion requires a separate verifier environment')
        receipt_path = self.trial_paths.trial_dir / 'daydream-receipt.json'
        receipt = json.loads(receipt_path.read_text())
        if (receipt.get('status') != 'succeeded'
                or type(receipt.get('return_code')) is not int or receipt['return_code'] != 0):
            raise RuntimeError('Daydream host invocation did not finish successfully')
        await self.environment.upload_file(receipt_path, '/tmp/aacr-host-receipt.json')
        # PiJudgeVerifier uploads the judge login, then Harbor's native verify
        # uploads the trusted tests, runs them and parses the reward.
        return await super().verify()
