"""Catch the judge drifting: score the canary cases with the oracle and compare them with the reference.

    .venv/bin/python -m loop.canary --name canary-<date>
    .venv/bin/python -m loop.canary --summary artifacts/loop/<name>/summary.json [--record]

The canary cases (``loop/canary.txt``) are the climb set's first ``canary_cases``.
``--name`` scores them in one oracle round with ``loop.rounds``; ``--summary`` takes
such a round already scored. Each case's counts are compared with the reference in the
``canary`` block of ``loop/noise.json`` (#45): a count that moved from it by more than
its ``tolerance``, which #45 derived from the judge's own round-to-round noise, or a
case left unscored, is drift. The result is JSON for the ledger, on stdout, and the
command exits 0 when stable, 1 when drifted, and 2 when nothing could be compared:
``no-reference`` without a reference, ``invalid`` for a round that isn't one oracle
round of the canary cases, one scored by another judge, ReviewBench revision or split
than the reference's, or one that failed outright (more than a fifth of its trials
unscored, which ``loop.rounds`` refuses). ``loop.run`` stops on either: drift as
``judge-drift``, no verdict as ``infrastructure``. ``--name`` must be new, since
``loop.rounds`` would otherwise return the round it scored before; a name whose round
failed is scored afresh.

``--record`` makes the round the new reference instead, keeping the tolerance, and
prints the reference it replaced. Only a person runs it, after deciding the judge
changed; every baseline is then measured again.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from loop import rounds, sets

NOISE = Path(__file__).with_name('noise.json')
CASES = sets.CANARY
# What a round must share with the reference, besides its judge.
IDENTITY = ('reviewbench_revision', 'split')


class NoReference(LookupError):
    pass


def canary_block(noise: Path, referenced: bool = True) -> dict:
    """noise.json's canary block; with ``referenced``, it must hold every case's reference."""
    try:
        block = json.loads(Path(noise).read_text()).get('canary')
    except FileNotFoundError:
        raise NoReference(f'No {noise}; derive it with python -m loop.noise (#45)') from None
    if not block or 'tolerance' not in block:
        raise NoReference(f'No canary block in {noise}; derive it with python -m loop.noise (#45)')
    unreferenced = [case for case in block['cases'] if case not in block.get('reference', {})]
    if referenced and unreferenced:
        raise NoReference(f'No reference counts for {unreferenced} in {noise}')
    # A reference derived by loop.noise shares the measurement's catalog; a recorded one keeps its own.
    catalog = json.loads(Path(noise).read_text()).get('catalog', {})
    block.setdefault('catalog', {key: catalog.get(key) for key in IDENTITY})
    return block


def observed(summary: dict, cases: list[str]) -> tuple[str, str, dict]:
    """The job, judge and each case's counts (None where unscored) of one oracle round of the cases."""
    build = summary.get('request', {}).get('build')
    if build != 'oracle':
        raise ValueError(f'A canary round is scored with --build oracle, not {build!r}')
    if len(summary.get('rounds', [])) != 1:
        raise ValueError(f'A canary summary holds one finished round, not {len(summary.get("rounds", []))}')
    absent = [case for case in cases if case not in summary['cases']]
    if absent:
        raise ValueError(f'The round did not include the canary cases {absent}')
    return (summary['rounds'][0]['job'], summary['catalog_identity']['judge'],
            {case: summary['cases'][case][0] for case in cases})


def compare(block: dict, summary: dict) -> dict:
    """Each canary count against its reference; a case moved beyond the tolerance, or unscored, is drift."""
    job, judge, seen = observed(summary, block['cases'])
    if block.get('judge') not in (None, judge):
        raise ValueError(f'The reference was recorded with judge {block["judge"]}, not {judge}')
    for key, expected in block.get('catalog', {}).items():
        if expected is not None and summary['catalog_identity'].get(key) != expected:
            raise ValueError(f'The reference was recorded on {key} {expected}, '
                             f'not {summary["catalog_identity"].get(key)}')
    tolerance, cases, moved = block['tolerance'], {}, []
    for case in block['cases']:
        reference, counts = block['reference'][case], seen[case]
        cases[case] = {key: dict(reference=reference[key], observed=None if counts is None else counts[key],
                                 delta=None if counts is None else counts[key] - reference[key])
                       for key in block['counts']}
        if counts is None or any(abs(entry['delta']) > tolerance for entry in cases[case].values()):
            moved.append(case)
    return dict(verdict='drifted' if moved else 'stable', job=job, judge=judge, tolerance=tolerance,
                cases=cases, moved=moved, unscored=[case for case in block['cases'] if seen[case] is None])


def record(noise: Path, summary: dict, source: Path) -> dict:
    """Make a fully scored round the canary's reference, keeping its tolerance; return the new block."""
    data = json.loads(Path(noise).read_text())
    block = canary_block(noise, referenced=False)
    job, judge, seen = observed(summary, block['cases'])
    unscored = [case for case, counts in seen.items() if counts is None]
    if unscored:
        raise ValueError(f'Refusing to record a round that left {unscored} unscored')
    block.update(reference={case: {key: seen[case][key] for key in block['counts']} for case in block['cases']},
                 judge=judge, catalog={key: summary['catalog_identity'].get(key) for key in IDENTITY},
                 source=dict(summary=str(source), job=job))
    data['canary'] = block
    temporary = Path(noise).with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')
    os.replace(temporary, noise)
    return block


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--name', help='A fresh loop.rounds name for one oracle round of loop/canary.txt')
    source.add_argument('--summary', type=Path, help='A loop.rounds summary of one oracle round, scored already')
    parser.add_argument('--noise', type=Path, default=NOISE, help='Holds the canary reference (default loop/noise.json)')
    parser.add_argument('--record', action='store_true',
                        help='Make this round the reference, after a person decided the judge changed')
    arguments = parser.parse_args(argv)
    try:
        # Checked before a round runs, so a missing reference costs no judge time.
        block = canary_block(arguments.noise, referenced=not arguments.record)
        if arguments.name:
            path = rounds.ARTIFACTS / arguments.name / 'summary.json'
            # A round scored under this name would be returned again; one that failed is scored afresh.
            if path.exists() and any(job.get('state') == 'finished'
                                     for job in json.loads(path.read_text()).get('jobs', [])):
                raise ValueError(f'{arguments.name} was scored already; give the canary a new name')
            summary = rounds.run('oracle', CASES, 1, arguments.name)
        else:
            path, summary = arguments.summary, json.loads(arguments.summary.read_text())
        if arguments.record:
            replaced = {key: block.get(key) for key in ('reference', 'judge', 'catalog', 'source')}
            print(json.dumps(dict(recorded=str(arguments.noise), replaced=replaced,
                                  canary=record(arguments.noise, summary, path)), indent=2, allow_nan=False))
            return 0
        result = compare(block, summary)
    except NoReference as error:
        result = dict(verdict='no-reference', error=str(error))
    except Exception as error:
        # A failed round, or a malformed reference or summary, is no verdict on the judge, never drift.
        result = dict(verdict='invalid', error=f'{type(error).__name__}: {error}')
    print(json.dumps(result, indent=2, allow_nan=False))
    for case in result.get('moved', []):
        moves = [f'{key} {entry["delta"]:+g}' for key, entry in result['cases'][case].items()
                 if entry['delta'] is not None and abs(entry['delta']) > result['tolerance']]
        print(f'{case} moved: {", ".join(moves) or "unscored"}', file=sys.stderr)
    return {'stable': 0, 'drifted': 1}.get(result['verdict'], 2)


if __name__ == '__main__':
    sys.exit(main())
