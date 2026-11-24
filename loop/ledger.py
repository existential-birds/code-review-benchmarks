"""The ledger: an append-only record of every iteration of a loop run.

A run keeps ``loop-runs/<run>/ledger.jsonl`` and ``loop-runs/<run>/patches/<n>.patch``.
Each line is one JSON object with ``schema_version``, ``run``, ``time`` (UTC, ISO 8601),
``prev_sha256`` (the SHA-256 of the previous line's bytes without its newline; null on
the first line), ``kind`` and that kind's fields, and no others:

- ``run-start`` (always the first line): ``settings`` (a copy), ``suite_commit``,
  ``judge``, ``baseline_commit``, ``baseline_summary_sha256`` and ``canary``.
- ``iteration``: ``iteration`` (its number n) and ``verdict``, an object with
  ``verdict`` (accepted-on-dev, rejected or invalid) and ``reasons`` (a list of
  strings), and nothing else. As far as it got: ``proposal`` (strings ``hypothesis``
  in one sentence, ``category`` from Daydream's trace-analysis skill, ``prediction``
  and ``rejection_criterion``; lists of strings ``evidence`` pointers and ``files``
  touched); ``candidate`` (``commit``,
  ``patch_id`` from ``git patch-id``, diff size, wheel SHA-256), whose patch is saved
  as patches/<n>.patch before the line is written; ``gate`` (the diff guard's result,
  ``make check``'s result and seconds); ``scores`` with ``climb`` and, when run,
  ``check``: per-round and mean precision, recall and F1 for both sides, the gain,
  the threshold, each guard, and the summary's path and SHA-256; ``cost`` (trials,
  reviewer seconds, API-equivalent cost, tokens, wall seconds, proposer usage).
- ``confirmation``: ``iteration`` (one accepted on dev) and ``verdict``, one word:
  confirmed, not-confirmed or regressed. No held-out score ever goes in the ledger,
  so a confirmation refuses any other field.
- ``stop``: ``reason``, one of ``STOPS``, and optionally ``detail``, a string saying
  which limit or failure (``loop.run``). A run resumed after a stop goes on appending.
- ``landing``: ``iteration`` (one confirmed), the draft pull request's ``url``, its
  ``branch`` and the ``commit`` it proposes (``loop.land``), once per iteration.
- ``merge``: ``iteration`` (one landed), the landing's ``url`` and the ``merge_commit``
  a person merged it as. A merged winner no longer waits for a person.

Lines are appended under an exclusive lock and fsynced, never rewritten or deleted.
JSON is strict: NaN and infinities are refused. Reading verifies the chain, so an
edited or removed line is caught, except the last: an edited or removed last line
is caught only against a line hash quoted elsewhere, which ``verify`` and ``show``
print. A last line without its newline is a torn write and is reported, never
dropped.

    python -m loop.ledger verify | show | ideas <run>...
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys

SCHEMA_VERSION = 1
RUNS = Path(__file__).resolve().parents[1] / 'loop-runs'
FIELDS = {  # kind: (required, optional)
    'run-start': ({'settings', 'suite_commit', 'judge', 'baseline_commit', 'baseline_summary_sha256', 'canary'}, set()),
    'iteration': ({'iteration', 'verdict'}, {'proposal', 'candidate', 'gate', 'scores', 'cost'}),
    'confirmation': ({'iteration', 'verdict'}, set()),
    'stop': ({'reason'}, {'detail'}),
    'landing': ({'iteration', 'url', 'branch', 'commit'}, set()),
    'merge': ({'iteration', 'url', 'merge_commit'}, set()),
}
PROPOSAL = {'hypothesis', 'category', 'evidence', 'prediction', 'rejection_criterion', 'files'}
VERDICTS = ('accepted-on-dev', 'rejected', 'invalid')
CONFIRMATIONS = ('confirmed', 'not-confirmed', 'regressed')
# Why loop.run stopped: no gain lately, a budget spent, a confirmation that regressed, the judge drifting,
# a winner waiting for a person, held-out cases used up, or broken builds, jobs, logins or limits.
STOPS = ('plateau', 'budget', 'held-out-regression', 'judge-drift', 'winner-awaiting-merge', 'held-out-worn',
         'infrastructure')


def _sha256(line: bytes) -> str:
    return hashlib.sha256(line).hexdigest()


def _strings(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _refuse_constant(name: str) -> None:
    raise ValueError(f'{name} is not strict JSON')


def _check(kind: str, fields: dict, earlier: list[dict]) -> None:
    """Refuse an entry that doesn't fit the schema or the entries before it."""
    if not isinstance(kind, str) or kind not in FIELDS:
        raise ValueError(f'Unknown ledger entry kind: {kind}')
    if bool(earlier) == (kind == 'run-start'):
        raise ValueError('A ledger starts with one run-start entry, and only there')
    required, optional = FIELDS[kind]
    if not required <= set(fields) <= required | optional:
        allowed = f' and may add {sorted(optional)}' if optional else ' only'
        raise ValueError(f'A {kind} entry holds {sorted(required)}{allowed}: got {sorted(fields)}')
    if kind == 'stop' and (not isinstance(fields['reason'], str) or not isinstance(fields.get('detail', ''), str)):
        raise ValueError(f'A stop reason is a string: {fields["reason"]!r}')
    if kind == 'stop' and fields['reason'] not in STOPS:
        raise ValueError(f'A stop reason is one of {STOPS}: {fields["reason"]!r}')
    if kind in ('iteration', 'confirmation', 'landing', 'merge'):
        n = fields['iteration']
        if type(n) is not int or n < 1:
            raise ValueError(f'An iteration number is a positive integer: {n!r}')
        iterations = {entry['iteration']: entry for entry in earlier if entry['kind'] == 'iteration'}
    if kind == 'iteration':
        if n in iterations:
            raise ValueError(f'Iteration {n} is already in the ledger')
        verdict = fields['verdict']
        if (not isinstance(verdict, dict) or set(verdict) != {'verdict', 'reasons'}
                or verdict['verdict'] not in VERDICTS or not _strings(verdict['reasons'])):
            raise ValueError(f'An iteration verdict is exactly {{"verdict": one of {VERDICTS}, "reasons": [str]}}: '
                             f'{verdict!r}')
        proposal = fields.get('proposal')
        if 'proposal' in fields and (
                not isinstance(proposal, dict) or not PROPOSAL <= set(proposal)
                or not all(isinstance(proposal[key], str) for key in ('hypothesis', 'category', 'prediction',
                                                                      'rejection_criterion'))
                or not _strings(proposal['evidence']) or not _strings(proposal['files'])):
            raise ValueError(f'A proposal needs {sorted(PROPOSAL)}: strings, but evidence and files are '
                             f'lists of strings')
        candidate = fields.get('candidate')
        if 'candidate' in fields and (not isinstance(candidate, dict) or not {'commit', 'patch_id'} <= set(candidate)):
            raise ValueError('A candidate needs its commit and patch_id')
        scores = fields.get('scores')
        if 'scores' in fields and (not isinstance(scores, dict) or 'climb' not in scores
                                   or not set(scores) <= {'climb', 'check'}):
            raise ValueError(f'Scores hold the climb set and, when run, the check set only: {scores!r}')
    if kind == 'confirmation':
        if fields['verdict'] not in CONFIRMATIONS:
            raise ValueError(f'A confirmation is one word, {CONFIRMATIONS}: {fields["verdict"]!r}')
        if iterations.get(n, {}).get('verdict', {}).get('verdict') != 'accepted-on-dev':
            raise ValueError(f'Only an iteration accepted on dev is confirmed, and iteration {n} is not')
        if any(entry['kind'] == 'confirmation' and entry['iteration'] == n for entry in earlier):
            raise ValueError(f'Iteration {n} is already confirmed')
    if kind in ('landing', 'merge'):
        if not all(isinstance(fields[key], str) and fields[key] for key in FIELDS[kind][0] - {'iteration'}):
            raise ValueError(f'A {kind} entry holds strings besides its iteration number')
        landed = [entry['url'] for entry in earlier if entry['kind'] == 'landing' and entry['iteration'] == n]
    if kind == 'landing':
        if not any(entry['kind'] == 'confirmation' and entry['iteration'] == n and entry['verdict'] == 'confirmed'
                   for entry in earlier):
            raise ValueError(f'Only a confirmed iteration is landed, and iteration {n} is not')
        if landed:
            raise ValueError(f'Iteration {n} is already landed: {landed[0]}')
    if kind == 'merge':
        if landed != [fields['url']]:
            raise ValueError(f'Iteration {n} was not landed as {fields["url"]}')
        if any(entry['kind'] == 'merge' and entry['iteration'] == n for entry in earlier):
            raise ValueError(f'Iteration {n} is already merged')


def _verified(run: Path) -> tuple[list[dict], list[bytes]]:
    path = Path(run) / 'ledger.jsonl'
    lines = (path.read_bytes() if path.exists() else b'').split(b'\n')
    if lines[-1]:
        raise ValueError(f'{path}: line {len(lines)} is torn (no newline), probably by a crash while '
                         f'writing. The ledger is append-only, so a person must decide what to do with it: '
                         f'{lines[-1][:200]!r}')
    lines.pop()
    entries = []
    for number, line in enumerate(lines, 1):
        try:
            try:
                entry = json.loads(line, parse_constant=_refuse_constant)
            except json.JSONDecodeError:
                entry = None
            if not isinstance(entry, dict):
                raise ValueError('it is not a JSON object')
            if entry.get('prev_sha256') != (_sha256(lines[number - 2]) if number > 1 else None):
                raise ValueError("its prev_sha256 is not the previous line's SHA-256, so a line was "
                                 'edited, removed or inserted')
            if entry.get('schema_version') != SCHEMA_VERSION or entry.get('run') != Path(run).name:
                raise ValueError(f'it is not a schema {SCHEMA_VERSION} entry of run {Path(run).name}')
            if not isinstance(entry.get('time'), str):
                raise ValueError('it has no time')
            fields = {key: value for key, value in entry.items()
                      if key not in ('schema_version', 'run', 'time', 'prev_sha256', 'kind')}
            _check(entry.get('kind'), fields, entries)
        except ValueError as error:
            raise ValueError(f'{path}: line {number}: {error}') from None
        entries.append(entry)
    return entries, lines


def read(run: Path) -> list[dict]:
    """Every entry of a run's ledger, after verifying the chain and each entry."""
    return _verified(run)[0]


def _write(file, data: bytes) -> None:
    file.write(data)
    file.flush()
    os.fsync(file.fileno())


def append(run: Path, kind: str, fields: dict, patch: str | None = None) -> dict:
    """Check and append one entry, saving an iteration's patch first; return the entry.

    The ledger stays locked from verifying it to writing the line, so two writers
    can't both chain to the same line.
    """
    run = Path(run)
    run.mkdir(parents=True, exist_ok=True)
    with open(run / 'ledger.jsonl', 'ab') as ledger:
        fcntl.flock(ledger, fcntl.LOCK_EX)
        entries, lines = _verified(run)
        _check(kind, fields, entries)
        if (patch is None) != ('candidate' not in fields):
            raise ValueError('An iteration with a candidate saves its patch, and only then')
        entry = {'schema_version': SCHEMA_VERSION, 'run': run.name,
                 'time': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                 'prev_sha256': _sha256(lines[-1]) if lines else None, 'kind': kind, **fields}
        line = json.dumps(entry, allow_nan=False).encode()
        if patch is not None:
            (run / 'patches').mkdir(exist_ok=True)
            with open(run / 'patches' / f'{fields["iteration"]}.patch', 'wb') as file:
                _write(file, patch.encode())
        _write(ledger, line + b'\n')
    return entry


def _one_line(text: str) -> str:
    return ' '.join(text.split())


def ideas(runs: list[Path]) -> list[str]:
    """One line per past proposal: hypothesis, category, files touched, verdict and reason.

    A run with no ledger yet has no proposals.
    """
    lines = []
    for run in runs:
        entries = read(run)
        confirmed = {entry['iteration']: entry['verdict'] for entry in entries if entry['kind'] == 'confirmation'}
        for entry in entries:
            if entry['kind'] != 'iteration' or 'proposal' not in entry:
                continue
            proposal, verdict = entry['proposal'], entry['verdict']['verdict']
            if entry['iteration'] in confirmed:
                verdict += f', then {confirmed[entry["iteration"]]} on held-out'
            lines.append(_one_line(f'{Path(run).name} #{entry["iteration"]} {verdict} [{proposal["category"]}] '
                                   f'{proposal["hypothesis"]} | files: {", ".join(proposal["files"])} '
                                   f'| reason: {"; ".join(entry["verdict"]["reasons"]) or "none given"}'))
    return lines


def _run_name(name: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name):
        raise argparse.ArgumentTypeError(f'A run is a folder name under loop-runs/: {name!r}')
    return name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('verify', 'show', 'ideas'))
    parser.add_argument('runs', nargs='+', type=_run_name, metavar='run')
    arguments = parser.parse_args(argv)
    runs = [RUNS / name for name in arguments.runs]
    try:
        if arguments.command == 'ideas':
            for line in ideas(runs):
                print(line)
            return 0
        for run in runs:
            entries, lines = _verified(run)
            if not entries:
                raise ValueError(f'No ledger entries in {run / "ledger.jsonl"}')
            if arguments.command == 'verify':
                print(f'{run.name}: {len(entries)} entries, chain intact, last line sha256 {_sha256(lines[-1])}')
            else:
                for number, (line, entry) in enumerate(zip(lines, entries), 1):
                    print(f'# {run.name} line {number} sha256 {_sha256(line)}\n{json.dumps(entry, indent=2)}')
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
