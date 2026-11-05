"""Pass the host's record of the review process to Harbor's separate verifier."""
from __future__ import annotations

import json

from benchmarks.pi.verifier import PiJudgeVerifier

# Where a benchmark's verifier finds the host's record of the review process.
HOST_RECEIPT = '/tmp/daydream-host-receipt.json'


class DaydreamVerifier(PiJudgeVerifier):
    """Verify only after the host saw Daydream exit successfully."""

    async def verify(self):
        # The reviewer can write the verifier's log mount, so discard any reward
        # there before checking the receipt.
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
        await self.environment.upload_file(receipt_path, HOST_RECEIPT)
        # PiJudgeVerifier uploads the judge login, then Harbor uploads the tests,
        # runs them and reads the reward.
        return await super().verify()
