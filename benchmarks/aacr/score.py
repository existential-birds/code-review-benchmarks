"""Fail-closed boundary around AACR's unchanged single-case evaluator."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path

from benchmarks.aacr.upstream import config, evaluate, judge, schema


class VerificationError(RuntimeError):
    """This trial cannot be graded."""


def _write_json(path: Path, value) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def _validate_findings(path: Path) -> dict:
    result = json.loads(path.read_text())
    if not isinstance(result, dict) or not isinstance(result.get('review_output'), list):
        raise VerificationError('Expected a completed review_output array')
    for finding in result['review_output']:
        if not isinstance(finding, dict):
            raise VerificationError('Finding must be an object')
        if not isinstance(finding.get('file'), str) or not finding['file'].strip():
            raise VerificationError('Finding requires file context')
        for key in ('summary', 'description'):
            if not isinstance(finding.get(key, ''), str):
                raise VerificationError('Finding text must be a string')
        if not any(finding.get(key, '').strip() for key in ('summary', 'description')):
            raise VerificationError('Finding requires text')
        for key in ('start_line', 'end_line'):
            line = finding.get(key)
            if line is not None and (type(line) is not int or line <= 0):
                raise VerificationError('Line anchors must be positive integers or null')
    return result


class _VerdictGuard:
    """Remember failures AACR catches, without replacing its matching parser."""
    def __init__(self, client, output: Path):
        self.client = client
        self.output = output
        self.failures = []
        self.verdicts = []
        self.chat = self.completions = self

    async def create(self, **request):
        evidence = {'request': request}
        self.verdicts.append(evidence)
        try:
            response = await self.client.chat.completions.create(**request)
            content = response.choices[0].message.content
            evidence['response'] = content
            if not isinstance(content, str) or not content.strip():
                raise VerificationError('Judge returned no text')
            # Match precisely AACR's last recognized bracketed-array rule. Do
            # not impose last-line placement or alter its index filtering.
            matches = re.findall(r'\[[\d,\s]*\]', content.strip())
            if not matches:
                raise VerificationError('Judge returned no verdict array')
            json.loads(matches[-1])  # catches arrays such as [1,] AACR swallows
            return response
        except Exception as error:
            failure = {'category': 'verdict' if isinstance(error, (VerificationError, json.JSONDecodeError))
                       else 'judge_call', 'error_type': type(error).__name__}
            self.failures.append(failure)
            evidence['failure'] = failure
            raise
        finally:
            try:
                _write_json(self.output / 'verdicts.json', self.verdicts)
            except Exception as error:
                self.failures.append({'category': 'evidence_write', 'error_type': type(error).__name__})
                raise


async def score_case(instance_path: Path, findings_path: Path, output_dir: Path, *, client=None) -> dict:
    """Grade exactly one completed review, publishing rewards only on success."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    diagnostic = {'status': 'started', 'stage': 'inputs', 'failures': []}
    _write_json(output_dir / 'diagnostic.json', diagnostic)
    guard = None
    try:
        instances = schema.load_instances(instance_path)
        if len(instances) != 1:
            raise VerificationError('Exactly one review case is required')
        findings = _validate_findings(Path(findings_path))
        instance = instances[0]
        # Use AACR's own finding loader and exact case filename. These are
        # verifier evidence, never reviewer context or instructions.
        results_dir = output_dir / 'findings'
        results_dir.mkdir(exist_ok=True)
        _write_json(config.result_path(results_dir, instance.instance_id), findings)
        if client is None:
            from benchmarks.aacr.transport import PiJudgeClient
            client = PiJudgeClient(output_dir / 'judge')
        guard = _VerdictGuard(client, output_dir)
        diagnostic['stage'] = 'judge'
        _write_json(output_dir / 'diagnostic.json', diagnostic)
        old_mock, old_client = judge.USE_MOCK_LLM, judge._llm_client
        old_model = os.environ.get(config.JUDGE_MODEL_VAR)
        # AACR otherwise silently selects mock mode when no API key exists.
        # Install the real adapter explicitly; no dummy key and no fallback.
        judge.USE_MOCK_LLM = False
        judge._llm_client = guard
        os.environ[config.JUDGE_MODEL_VAR] = 'gpt-6.1-sol'
        try:
            result = await evaluate.evaluate(instances, results_dir, 'codex', output_dir / 'upstream')
        finally:
            judge.USE_MOCK_LLM, judge._llm_client = old_mock, old_client
            if old_model is None:
                os.environ.pop(config.JUDGE_MODEL_VAR, None)
            else:
                os.environ[config.JUDGE_MODEL_VAR] = old_model
        diagnostic['failures'] = guard.failures + client.failures
        if diagnostic['failures']:
            raise VerificationError('Judge failed; see retained evidence')
        summary = result['summary']
        if (any(summary[key] != 1 for key in ('total_instances', 'candidate_instances', 'evaluated_instances'))
                or summary['missing_instances'] != 0
                or result['ex_info']['missing_instance_ids']
                or [entry['instance_id'] for entry in result['eval_res']] != [instance.instance_id]):
            raise VerificationError('The intended review case was not evaluated exactly once')
        _write_json(output_dir / 'score.json', result)
        rewards = {key: summary[key] for key in (
            'file_semantic_match_rate', 'file_semantic_recall_rate', 'balanced_score',
            'expected_notes', 'generated_notes', 'matched_generated_notes', 'matched_file_semantic_notes',
        )}
        diagnostic.update(status='succeeded', stage='complete')
        _write_json(output_dir / 'diagnostic.json', diagnostic)
        _write_json(output_dir / 'reward.json', rewards)
        return result
    except Exception as error:
        if guard is not None:
            diagnostic['failures'] = guard.failures + client.failures
        diagnostic.update(status='failed', error_type=type(error).__name__)
        _write_json(output_dir / 'diagnostic.json', diagnostic)
        raise VerificationError('Verification failed; see diagnostic.json') from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance', type=Path, required=True)
    parser.add_argument('--findings', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(score_case(args.instance, args.findings, args.output))
    except VerificationError:
        print('AACR verification failed; inspect verifier/diagnostic.json and judge evidence.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
