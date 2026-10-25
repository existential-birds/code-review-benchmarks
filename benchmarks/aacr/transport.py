"""AACR's narrow async text-completion interface backed by Pi's subscription SDK.

No credentials pass through this process or its evidence. Pi reads and refreshes
its native store inside the separate verifier.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace


class JudgeTransportError(RuntimeError):
    """A judge request failed; upstream may catch it, so also retain a sticky flag."""


class PiJudgeClient:
    def __init__(self, evidence_dir: Path):
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.failures: list[dict] = []
        self.chat = SimpleNamespace(completions=self)
        self._request_count = 0

    def _write(self, request_id: int, evidence: dict) -> None:
        path = self.evidence_dir / f"judge-call-{request_id:04d}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
        temporary.replace(path)

    async def create(self, **request):
        self._request_count += 1
        request_id = self._request_count
        started = time.monotonic()
        evidence = {"request_id": request_id, "request": request, "status": "started"}
        self._write(request_id, evidence)
        process = None
        category = "subprocess"
        details = {}
        try:
            process = await asyncio.create_subprocess_exec(
                "node", str(Path(__file__).with_name("pi_judge.mjs")),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await process.communicate(json.dumps(request).encode())
            evidence["exit_code"] = process.returncode
            try:
                result = json.loads(stdout)
            except (ValueError, UnicodeDecodeError):
                category = "subprocess" if process.returncode else "protocol"
                raise JudgeTransportError("Pi helper returned no valid completion") from None
            if isinstance(result, dict) and result.get("ok") is False:
                error = result.get("error")
                if not isinstance(error, dict):
                    category = "protocol"
                    raise JudgeTransportError("Pi helper returned invalid failure evidence")
                reported = error.get("category")
                category = reported if reported in {"authentication", "transport", "output", "runtime", "request"} else "protocol"
                status = error.get("http_status")
                if type(status) is int and 100 <= status <= 599:
                    details["http_status"] = status
                if error.get("stop_reason") in {"error", "aborted"}:
                    details["stop_reason"] = error["stop_reason"]
                raise JudgeTransportError("Pi subscription judge request failed")
            if process.returncode:
                raise JudgeTransportError("Pi helper subprocess failed")
            category = "protocol"
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise JudgeTransportError("Pi helper completion is invalid")
            text = result.get("text")
            metadata = result.get("metadata", {})
            if (
                not isinstance(text, str) or not text.strip()
                or metadata.get("provider") != "openai-codex"
                or metadata.get("model") != "gpt-6.1-sol"
                or metadata.get("subscription") is not True
                or metadata.get("sdk_version") != "1.4.0"
                or metadata.get("pi_ai_version") != "1.4.0"
                or metadata.get("stop_reason") not in {"stop", "length"}
            ):
                raise JudgeTransportError("Pi helper did not return a subscription text completion")
            evidence.update(status="completed", response=result)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
        except (Exception, asyncio.CancelledError) as error:
            if isinstance(error, (TimeoutError, asyncio.CancelledError)):
                category = "transport"
                if process is not None and process.returncode is None:
                    process.kill()
                    await process.wait()
            failure = {
                "request_id": request_id,
                "category": category,
                "message": "Pi subscription judge request failed; inspect category and exit code",
                **details,
            }
            self.failures.append(failure)
            evidence.update(status="failed", failure=failure)
            if isinstance(error, asyncio.CancelledError):
                raise
            raise JudgeTransportError(f"Pi judge failure: {category}") from None
        finally:
            evidence["elapsed_seconds"] = round(time.monotonic() - started, 6)
            self._write(request_id, evidence)
