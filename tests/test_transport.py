"""Transport-only doubles; scoring behavior is tested through AACR separately."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from benchmarks.aacr.transport import PiJudgeClient, JudgeTransportError


class PiJudgeClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.client = PiJudgeClient(self.path)

    def process(self, payload, returncode=0, stderr=b""):
        return SimpleNamespace(
            returncode=returncode,
            communicate=AsyncMock(return_value=(json.dumps(payload).encode(), stderr)),
            kill=lambda: None,
            wait=AsyncMock(),
        )

    def success(self, text="Full rationale\n[1]"):
        return {
            "ok": True,
            "text": text,
            "metadata": {
                "provider": "openai-codex", "model": "gpt-6.1-sol",
                "subscription": True, "sdk_version": "1.5.0",
                "pi_ai_version": "1.5.0", "stop_reason": "stop",
                "usage": {"input": 20, "output": 5},
            },
        }

    async def call(self):
        return await self.client.chat.completions.create(
            model="gpt-6.1-sol", messages=[{"role": "user", "content": "AACR prompt"}],
            temperature=0, max_tokens=3000, top_p=0.95,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )

    async def test_full_text_and_requested_options_are_retained(self):
        text = "A" * 800 + "\n[1]"
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=self.process(self.success(text)))) as spawn:
            response = await self.call()
        self.assertEqual(response.choices[0].message.content, text)
        self.assertEqual(self.client.failures, [])
        saved = json.loads(next(self.path.glob("judge-call-*.json")).read_text())
        self.assertEqual(saved["response"]["text"], text)
        self.assertEqual(saved["request"]["top_p"], 0.95)
        self.assertEqual(saved["request"]["messages"][0]["content"], "AACR prompt")
        self.assertEqual(saved["response"]["metadata"]["usage"]["input"], 20)
        self.assertEqual(spawn.call_args.args[0], "node")

    async def test_authentication_failure_is_sticky_and_safe(self):
        failure = {"ok": False, "error": {"category": "authentication", "message": "Subscription authentication failed", "http_status": 401, "stop_reason": "error"}}
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=self.process(failure, 1, b"secret credential"))):
            with self.assertRaises(JudgeTransportError):
                await self.call()
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=self.process(self.success("[]")))):
            await self.call()
        self.assertEqual(self.client.failures[0]["category"], "authentication")
        self.assertEqual(self.client.failures[0]["http_status"], 401)
        self.assertEqual(self.client.failures[0]["stop_reason"], "error")
        self.assertEqual(len(self.client.failures), 1)
        self.assertNotIn("secret credential", "".join(p.read_text() for p in self.path.iterdir()))

    async def test_subprocess_and_protocol_failures_are_diagnostic(self):
        for payload, code, category in [
            ({}, 2, "subprocess"),
            ({}, 0, "protocol"),
            (self.success(None), 0, "protocol"),
            (self.success(" "), 0, "protocol"),
        ]:
            with self.subTest(payload=payload, code=code):
                with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=self.process(payload, code))):
                    with self.assertRaises(JudgeTransportError):
                        await self.call()
                self.assertEqual(self.client.failures[-1]["category"], category)
        self.assertEqual(len(list(self.path.glob("judge-call-*.json"))), 4)

    async def test_failed_spawn_still_records_diagnostic(self):
        with patch("asyncio.create_subprocess_exec", AsyncMock(side_effect=OSError("private token"))):
            with self.assertRaises(JudgeTransportError):
                await self.call()
        saved = json.loads(next(self.path.glob("judge-call-*.json")).read_text())
        self.assertEqual(saved["failure"]["category"], "subprocess")
        self.assertNotIn("private token", json.dumps(saved))

    async def test_completion_can_outlive_the_former_adapter_deadline(self):
        # Harbor may allow a verifier longer than 600 seconds. Advance only the
        # event-loop clock so a private adapter deadline fails without sleeping.
        loop = asyncio.get_running_loop()
        clock = [loop.time()]
        process = self.process(self.success())
        process.returncode = None
        process.kill = Mock(side_effect=lambda: setattr(process, "returncode", -9))

        async def communicate(_data):
            clock[0] += 601
            for _ in range(6):
                await asyncio.sleep(0)
            process.returncode = 0
            return json.dumps(self.success()).encode(), b""

        process.communicate = communicate
        debug = loop.get_debug()
        loop.set_debug(False)
        await asyncio.sleep(0)
        try:
            with patch.object(loop, "time", lambda: clock[0]), patch(
                "asyncio.create_subprocess_exec", AsyncMock(return_value=process)
            ):
                response = await self.call()
        finally:
            loop.set_debug(debug)
        self.assertEqual(response.choices[0].message.content, "Full rationale\n[1]")
        self.assertEqual(self.client.failures, [])
        process.kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
