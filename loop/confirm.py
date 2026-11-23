"""Confirm a change kept on dev on the held-out split, outside the loop (#54).

    .venv/bin/python -m loop.confirm --baseline <commit | pinned> --candidate <commit>

This is the only loop code that generates held-out catalogs: ReviewBench's test split
(``--split test``) and AACR's held-out cases (``--split holdout``, from
``benchmarks/aacr/holdout.txt``). ``loop.rounds`` and ``loop.decide`` refuse them. Each
side, on each benchmark, is scored over ``holdout_rounds`` rounds with ``loop.rounds``'
machinery: every round a fresh ``harbor run`` job with ``-k 1 -r 0``. A candidate is a
Daydream commit built with ``python -m benchmarks.daydream.build <clone> --candidate
<commit>``; the baseline is ``pinned`` or another such commit.

The two are compared on the review cases every round of both sides scored, each round
pooled again over those cases alone with the benchmark's own metric (``loop.decide``'s
arithmetic for ReviewBench, AACR's ``metric.py`` for AACR). Each margin is ``accept_z``
standard deviations of a difference of two means, from these rounds' own spread:
``accept_z * sqrt(sd_baseline**2 / R_baseline + sd_candidate**2 / R_candidate)``, with
each side's sample standard deviation (n-1) over its R rounds. The verdict
(``holdout_rule``) is one word:

- ``regressed``: the candidate's ReviewBench grounded F1 or AACR balanced score is below
  the baseline's by more than its margin;
- ``confirmed``: not regressed, ReviewBench grounded F1 is above the baseline's, and
  neither ReviewBench augmented precision nor AACR match rate is below the baseline's by
  more than its margin;
- ``not-confirmed``: anything else.

The command prints the word alone and exits 0. Everything else, Harbor and the
converters included, goes to ``confirmations/<candidate>/confirm.log``; follow progress
there. A run that can't give a verdict says why on stderr and exits 2.

Storage, all under ``confirmations/`` (ignored by Git), for people:
``baselines/<commit>/<benchmark>/`` holds a baseline's catalog, ``summary.json`` and
Harbor jobs, reused by every candidate confirmed against it; ``<candidate>/<benchmark>/``
the candidate's; ``<candidate>/result.json`` every score and margin. The loop reads only
``<candidate>/verdict.json``, through ``verdict``: ``{"verdict": "<word>"}`` and nothing
else. No held-out score goes to the ledger, a pull request or the proposer.

Counting: ``uses.json`` counts the verdicts given on each held-out set. ReviewBench's
test set starts at 1, since it was scored once before the loop. Once a set reaches
``holdout_max_uses`` the command refuses: fresh held-out cases are needed (#58).

A candidate is confirmed once, against one baseline, with no retry after any verdict.
A run that stops for infrastructure reasons, a failed or abandoned round, gives no
verdict and isn't counted: run the same command again and it resumes, scoring that
round as a fresh job and reusing the finished ones.

``--limit-cases N`` is for checking the command only: a pilot on the first N cases of
each held-out set, written under ``confirmations/pilots/<N>/`` in the same layout and
kept out of the use count. The loop never reads a pilot's verdict.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import fcntl
import json
import math
import os
from pathlib import Path
import re
import statistics
import sys
import tempfile

from benchmarks.aacr import convert as aacr_convert
from benchmarks.daydream import build
from benchmarks.reviewbench import convert
from loop import decide, rounds, settings
from loop.ledger import CONFIRMATIONS as WORDS

CONFIRMATIONS = Path('confirmations')
RULE = 'no-worse-and-reviewbench-test-gain'
# Each benchmark's held-out split, as its converter names it.
HELD_OUT = {'reviewbench': 'test', 'aacr': 'holdout'}
# What the rule reads on each benchmark: the score that must not regress, then the guard.
READS = {'reviewbench': ('grounded_f1', 'augmented_precision'), 'aacr': ('balanced_score', 'file_semantic_match_rate')}
# ReviewBench's test set was scored once before the loop existed.
FIRST_USES = {'reviewbench-test': 1, 'aacr-holdout': 0}
FRESH_CASES_ISSUE = 'https://github.com/existential-birds/code-review-benchmarks/issues/58'
COMMIT = re.compile(r'[0-9a-f]{40}')


def home(candidate: str, limit: int | None = None, root: Path = CONFIRMATIONS) -> Path:
    """The candidate's folder: ``confirmations/<candidate>``, or a pilot's under ``pilots/<N>``."""
    if not COMMIT.fullmatch(candidate):
        raise ValueError(f'A candidate is a full Daydream commit, 40 lowercase hex digits: {candidate!r}')
    if limit is not None and limit < 1:
        raise ValueError('--limit-cases must be positive')
    return (root if limit is None else root / 'pilots' / str(limit)) / candidate


def verdict(candidate: str, root: Path = CONFIRMATIONS) -> str | None:
    """The loop's only read of a confirmation: its word, or None before one is given."""
    path = home(candidate, root=root) / 'verdict.json'
    if not path.exists():
        return None
    written = json.loads(path.read_text())
    if not isinstance(written, dict) or set(written) != {'verdict'} or written['verdict'] not in WORDS:
        raise ValueError(f'{path} must hold {{"verdict": one of {WORDS}}} and nothing else')
    return written['verdict']


def uses(root: Path = CONFIRMATIONS) -> dict[str, int]:
    """The verdicts given on each held-out set so far."""
    path = root / 'uses.json'
    return json.loads(path.read_text()) if path.exists() else dict(FIRST_USES)


def _build(commit: str) -> tuple[str, str]:
    """A side's commit and its ``loop.rounds`` build: ``pinned``, or the candidate's build.json."""
    if commit in ('pinned', build.BUILD['source_commit']):
        return build.BUILD['source_commit'], 'pinned'
    if not COMMIT.fullmatch(commit):
        raise ValueError(f"A build is 'pinned' or a full Daydream commit, 40 lowercase hex digits: {commit!r}")
    path = build.CANDIDATES / commit / 'build.json'
    if not path.is_file():
        raise FileNotFoundError(f'No build of {commit}: run python -m benchmarks.daydream.build <daydream-clone> '
                                f'--candidate {commit}')
    if json.loads(path.read_text()).get('source_commit') != commit:
        raise ValueError(f'{path} is not a build of {commit}')
    return commit, str(path)


def _first_cases(benchmark: str, limit: int, upstream: Path) -> list[str]:
    """A pilot's cases: the first ``limit`` names of the held-out set, in name order."""
    if benchmark == 'aacr':
        names = [line for line in aacr_convert.HOLDOUT.read_text().splitlines() if line and not line.startswith('#')]
    else:
        upstream = Path(upstream).resolve()
        convert.check_source(upstream)
        with tempfile.TemporaryDirectory(prefix='loop-confirm-') as temporary:
            convert.export(upstream, Path(temporary) / 'test', 'test')
            names = [path.name for path in (Path(temporary) / 'test').iterdir() if path.is_dir()]
    return sorted(names)[:limit]


def _pooled(summary: dict, cases: list[str], benchmark: str) -> list[dict]:
    """Each round's scores pooled over ``cases`` alone, with the benchmark's own metric."""
    if benchmark == 'reviewbench':
        return decide.side_rounds(summary, cases)
    # AACR's per-case counts are exactly the reward fields its metric pools.
    return [rounds.BENCHMARKS[benchmark].pool([summary['cases'][case][number] for case in cases])
            for number in range(len(summary['rounds']))]


def compare(baseline: dict, candidate: dict, benchmark: str, limits: settings.Settings) -> dict:
    """Both sides' rounds on the matched cases, and each figure the rule reads: means and margin."""
    matched = decide.matched_cases(baseline, candidate)
    total, least = len(baseline['cases']), limits['min_matched_fraction']
    if not matched or len(matched) / total < least:
        raise ValueError(f'On {benchmark}, only {len(matched)} of {total} held-out cases were scored in every round '
                         f'of both sides, below the {least:g} needed to compare them')
    sides = dict(baseline=_pooled(baseline, matched, benchmark), candidate=_pooled(candidate, matched, benchmark))
    result = dict(matched=dict(cases=len(matched), of=total),
                  **{name: dict(rounds=entries, mean=decide.means(entries)) for name, entries in sides.items()},
                  rule={})
    for key in READS[benchmark]:
        values = {name: [entry[key] for entry in entries] for name, entries in sides.items()}
        if any(value is None for side in values.values() for value in side):
            raise ValueError(f'On {benchmark}, {key} is undefined in some round on the matched cases')
        result['rule'][key] = dict(
            baseline=statistics.fmean(values['baseline']), candidate=statistics.fmean(values['candidate']),
            stddev={name: statistics.stdev(side) for name, side in values.items()},
            margin=limits['accept_z'] * math.sqrt(sum(statistics.variance(side) / len(side)
                                                      for side in values.values())))
    return result


def rule(comparisons: dict) -> str:
    """The verdict from each benchmark's ``compare``."""
    reviewbench, aacr = comparisons['reviewbench']['rule'], comparisons['aacr']['rule']

    def below(figure: dict) -> bool:
        return figure['candidate'] < figure['baseline'] - figure['margin']

    if below(reviewbench['grounded_f1']) or below(aacr['balanced_score']):
        return 'regressed'
    if (reviewbench['grounded_f1']['candidate'] > reviewbench['grounded_f1']['baseline']
            and not below(reviewbench['augmented_precision']) and not below(aacr['file_semantic_match_rate'])):
        return 'confirmed'
    return 'not-confirmed'


def confirm(baseline: str, candidate: str, *, limit: int | None = None, concurrency: int | None = None,
            upstreams: dict[str, Path] | None = None, root: Path = CONFIRMATIONS) -> str:
    """Score both builds on every held-out set, write the result and the verdict, and return the word."""
    here = home(candidate, limit, root)
    limits = settings.load()
    planned = limits['holdout_rounds']
    if limits['holdout_rule'] != RULE:
        raise ValueError(f'loop.confirm applies holdout_rule {RULE!r}, not {limits["holdout_rule"]!r}')
    if planned < 2:
        raise ValueError('holdout_rounds must be at least 2, for each side to have a spread')
    base_commit, base_build = _build(baseline)
    if base_commit == candidate:
        raise ValueError('The candidate is the baseline')
    candidate_build = _build(candidate)[1]
    root.mkdir(parents=True, exist_ok=True)
    with open(root / 'lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another loop.confirm is running') from None
        if (here / 'verdict.json').exists():
            raise ValueError(f'{candidate} already has a verdict; a candidate is confirmed once, with no retry')
        count = uses(root)
        if spent := sorted(name for name, used in count.items() if used >= limits['holdout_max_uses']):
            raise ValueError(f'{", ".join(spent)} gave {limits["holdout_max_uses"]} verdicts, holdout_max_uses: '
                             f'fresh held-out cases are needed ({FRESH_CASES_ISSUE})')
        here.mkdir(parents=True, exist_ok=True)
        request = dict(baseline=base_commit, candidate=candidate, limit=limit)
        if (here / 'request.json').exists() and json.loads((here / 'request.json').read_text()) != request:
            raise ValueError(f'{candidate} was started as {json.loads((here / "request.json").read_text())}; '
                             'a confirmation keeps its baseline')
        rounds._write(here / 'request.json', request)
        comparisons = {}
        for benchmark, split in HELD_OUT.items():
            upstream = (upstreams or {}).get(benchmark, rounds.BENCHMARKS[benchmark].upstream)
            cases = None if limit is None else _first_cases(benchmark, limit, upstream)
            summaries = {}
            for side, commit, build_arg, folder in (
                    ('baseline', base_commit, base_build, here.parent / 'baselines' / base_commit / benchmark),
                    ('candidate', candidate, candidate_build, here / benchmark)):
                summaries[side] = rounds.score(build_arg, cases, planned, folder, f'{side}-{benchmark}',
                                               benchmark=benchmark, split=split, jobs_dir=folder / 'jobs',
                                               concurrency=concurrency, upstream=upstream)
                if summaries[side]['side'] != commit:
                    raise ValueError(f'The {side} {benchmark} rounds in {folder} ran {summaries[side]["side"]}, '
                                     f'not {commit}')
            comparisons[benchmark] = compare(summaries['baseline'], summaries['candidate'], benchmark, limits)
        word = rule(comparisons)
        rounds._write(here / 'result.json', dict(
            **request, counted=limit is None, verdict=word, holdout_rule=RULE, accept_z=limits['accept_z'],
            holdout_rounds=planned, benchmarks=comparisons))
        if limit is None:
            # Counted before the verdict is written, so a crash between the two can only over-count.
            rounds._write(root / 'uses.json', {name: used + 1 for name, used in count.items()})
        rounds._write(here / 'verdict.json', {'verdict': word})
    return word


@contextmanager
def _quiet(log: Path):
    """Send this process's output, and every command's it runs, to ``log``."""
    log.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout.flush()
    sys.stderr.flush()
    saved = os.dup(1), os.dup(2)
    with open(log, 'a', buffering=1) as file, redirect_stdout(file), redirect_stderr(file):
        os.dup2(file.fileno(), 1)
        os.dup2(file.fileno(), 2)
        try:
            yield
        finally:
            for number, fd in enumerate(saved, 1):
                os.dup2(fd, number)
                os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--baseline', required=True, help="'pinned', or a Daydream commit built as a candidate")
    parser.add_argument('--candidate', required=True, help='The Daydream commit to confirm, built as a candidate')
    parser.add_argument('--limit-cases', type=int, metavar='N',
                        help='A pilot to check the command only: the first N cases of each held-out set, '
                             'under confirmations/pilots/, kept out of the use count')
    parser.add_argument('--concurrency', type=int, help="Harbor's -n; defaults to the loop setting")
    arguments = parser.parse_args(argv)
    try:
        with _quiet(home(arguments.candidate, arguments.limit_cases) / 'confirm.log'):
            word = confirm(arguments.baseline, arguments.candidate, limit=arguments.limit_cases,
                           concurrency=arguments.concurrency)
    except (OSError, LookupError, ValueError, RuntimeError) as error:
        # No verdict and nothing counted; the message names no held-out score.
        print(error, file=sys.stderr)
        return 2
    print(word)
    return 0


if __name__ == '__main__':
    sys.exit(main())
