"""Score one Daydream build over rounds of ReviewBench or AACR cases with one command.

Harbor pools the trials of one job, so a round is its own job: one pass over the
cases with ``-k 1 -r 0``. This generates one catalog for the build, runs each round
with ``harbor run`` one after another, and summarises them::

    .venv/bin/python -m loop.rounds --build pinned --cases loop/climb.txt --rounds 5 --name baseline

Everything for a name stays in ``artifacts/loop/<name>/``: the catalog and
``summary.json``. The jobs are in Harbor's ``jobs/``. Run again with the same name,
it skips finished rounds and adds rounds up to ``--rounds``. A job that never
finished is never reopened, since resuming can delete trials: it is recorded as
abandoned. A round with more than a fifth of its trials unscored is recorded as
failed, since that is a lost login or an outage rather than noise. Either stops
the command, and a rerun scores that round again as a fresh job.

The summary's ``side`` and per-round counts come from the benchmark's round report
(``benchmarks.reviewbench.report`` or ``benchmarks.aacr.report``), which share their reading
and checks; a summary whose rounds all failed before any review has no side. Each round's scores pool its scored trials with
the benchmark's own metric; ``mean`` and ``stddev`` (sample, n-1) are over the
rounds that define a score. ``catalog_identity`` names the benchmark's revision,
split, judge (ReviewBench) or dataset (AACR) and build, so two summaries can be checked as comparable. ``cases`` keeps every
case's counts in every round (ReviewBench's overall counts, AACR's four reward counts), null where unscored, so two
summaries can be compared on the cases both scored.

Every catalog is generated from the dev split, so a held-out case is refused, and
``--name`` is a plain folder name, so nothing lands outside ``artifacts/loop/``. Held-out
rounds belong to ``loop.confirm`` alone: it is the one caller of ``score`` with a held-out
split, and keeps those rounds' folders and jobs under ``confirmations/``. ReviewBench is
the default and the only benchmark the loop scores. ``--benchmark aacr`` scores AACR dev
cases, to check its report and a build. A case that fails to convert leaves no catalog
behind: drop it and run again.

``--build oracle`` scores ReviewBench's own true findings with
``experiments/reviewbench-oracle.yaml`` instead of a Daydream review, so any spread
between its rounds is the judge's alone (#45). It still needs both login folders, since
the tasks mount both, but its trials record no build, and a trial counts as failed when it
left no score.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

from benchmarks.aacr import convert as aacr_convert, metric as aacr_metric, report as aacr_report
from benchmarks.daydream import build, rounds as shared
from benchmarks.reviewbench import convert, report
from benchmarks.reviewbench.metric import compute
from benchmarks.reviewbench.upstream.metric import read_counts
from loop import sets, settings

ARTIFACTS = Path('artifacts/loop')
JOBS = Path('jobs')
HARBOR = Path(sys.executable).with_name('harbor')
METRICS = ('grounded_precision', 'grounded_recall', 'grounded_f1',
           'augmented_precision', 'augmented_recall', 'augmented_f1')
COUNTS = ('expected', 'missing', 'evaluated', 'failed', 'incomplete', 'completed_empty', 'omitted_findings',
          'elapsed_seconds', 'cost_usd', 'cost_trials')
TOKENS = ('n_input_tokens', 'n_cache_tokens', 'n_output_tokens')
CATALOG = ('reviewbench_revision', 'split', 'judge', 'daydream')
# The task containers read the logins from these; the Makefile sets them, harbor run alone doesn't.
ORACLE_CONFIG = Path('experiments/reviewbench-oracle.yaml')
LOGINS = ('REVIEWER_CODEX_AUTH_DIR', 'JUDGE_PI_AUTH_DIR')
# A round with more unscored trials than this is failed, not finished: a lost login or a
# Docker outage, not noise (the stop rule of #45). The command stops; a rerun scores it afresh.
MAX_UNSCORED_FRACTION = 0.2
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')


def finished(job: str, jobs_dir: Path | None = None) -> bool:
    try:
        return json.loads(((jobs_dir or JOBS) / job / 'result.json').read_text()).get('finished_at') is not None
    except (OSError, ValueError):
        return False


def _reviewbench_pool(rewards: list[dict | None]) -> dict:
    pooled = compute(rewards)
    scored = pooled['scored_trials'] > 0
    return dict(findings_sent=pooled.get('candidate_count') if scored else None,
                unmatched_judged_wrong=pooled.get('unmatched_fp_count') if scored else None,
                **{key: pooled.get(key) for key in METRICS})


def _aacr_pool(rewards: list[dict | None]) -> dict:
    # AACR's precision divides by every finding sent; it has no judgement of unmatched findings.
    pooled = aacr_report.pool(rewards)
    return dict(findings_sent=pooled['generated_notes'] if pooled['evaluated_instances'] else None,
                **{key: pooled[key] for key in aacr_report.RATES})


def _aacr_identity(catalog: Path, listing: dict, expected: dict) -> dict:
    # AACR's catalog.json names no build; every task carries the one it was generated for.
    built = {(catalog / task / 'tests/build.json').read_text() for task in expected}
    if len(built) != 1:
        raise ValueError(f'{catalog} holds tasks for {len(built)} Daydream builds, not one')
    return {**{key: listing[key] for key in ('aacr_revision', 'dataset_sha256', 'split')},
            'daydream': json.loads(built.pop())}


# What differs between the benchmarks; the rounds, resumption and summary are the same.
BENCHMARKS = {
    'reviewbench': SimpleNamespace(
        report=report, config=Path('experiments/reviewbench.yaml'), upstream=Path('.cache/src/ReviewBench'),
        metrics=METRICS, pool=_reviewbench_pool,
        counts=lambda reward: read_counts(reward)['overall'],
        identity=lambda catalog, listing, expected: {key: listing[key] for key in CATALOG},
        generate=lambda upstream, catalog, cases, build_file, split: convert.generate_catalog(
            upstream, catalog, split=split, task_names=cases, build_file=build_file)),
    'aacr': SimpleNamespace(
        report=aacr_report, config=Path('experiments/aacr-baseline.yaml'), upstream=Path('.cache/src/aacr-bench'),
        metrics=aacr_report.RATES, pool=_aacr_pool,
        counts=lambda reward: {key: reward[key] for key in aacr_metric.COUNTS},
        identity=_aacr_identity,
        generate=lambda upstream, catalog, cases, build_file, split: aacr_convert.generate_catalog(
            upstream, catalog, daydream=True, split=split, task_names=cases, build_file=build_file)),
}


def summarise(catalog: Path, jobs: list[str], benchmark: str = 'reviewbench', jobs_dir: Path | None = None,
              oracle: bool = False) -> dict:
    """The catalog, side, every round's counts and pooled scores, their spread, and each case's counts."""
    bench = BENCHMARKS[benchmark]
    listing, expected = bench.report.read_catalog(catalog)
    entries = [bench.report.read_round((jobs_dir or JOBS) / job, expected) for job in jobs]
    # Rounds that all failed before any review started record no build.
    sides = shared.builds(entries)
    if oracle:
        # The oracle runs no review, so it leaves no review record: no build, and no status but its score.
        if sides - {None}:
            raise ValueError(f'An oracle summary records no build, not {sorted(map(str, sides))}')
        sides = set()
        for entry in entries:
            entry.update(produced=entry['evaluated'], completed_empty=0, incomplete=0,
                         failed=entry['expected'] - entry['missing'] - entry['evaluated'])
    elif len(sides) > 1 or None in sides:
        raise ValueError(f'One build per summary, not {sorted(map(str, sides))}')
    rounds, cases = [], {case: [None] * len(jobs) for case in expected}
    for number, entry in enumerate(entries, 1):
        rounds.append(dict(
            round=number, job=entry['job'], **{key: entry[key] for key in COUNTS},
            **{key: entry['harbor'][key] for key in TOKENS},
            **bench.pool([trial['reward'] for trial in entry['trials']])))
        for trial in entry['trials']:
            if trial['reward'] is not None:
                cases[trial['task']][number - 1] = bench.counts(trial['reward'])
    return dict(catalog_identity=bench.identity(catalog, listing, expected), side=next(iter(sides), None),
                rounds=rounds, scores={key: shared.spread([entry[key] for entry in rounds]) for key in bench.metrics},
                cases=cases)


def outcome(job: str, catalog: Path, benchmark: str = 'reviewbench', jobs_dir: Path | None = None) -> str:
    """finished, failed (too many trials unscored) or abandoned (the job never finished)."""
    if not finished(job, jobs_dir):
        return 'abandoned'
    bench = BENCHMARKS[benchmark].report
    entry = bench.read_round((jobs_dir or JOBS) / job, bench.read_catalog(catalog)[1])
    return 'failed' if entry['expected'] - entry['evaluated'] > MAX_UNSCORED_FRACTION * entry['expected'] else 'finished'


def _write(path: Path, state: dict) -> None:
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2, allow_nan=False) + '\n')
    os.replace(temporary, path)


def check_logins() -> None:
    """Refuse to start without the login folders, since every trial would fail at once."""
    missing = [name for name in LOGINS if not os.environ.get(name) or not Path(os.environ[name]).is_dir()]
    if missing:
        raise ValueError(f'Set {" and ".join(missing)} to the reviewer and judge login folders, as the Makefile does')


def run(build: str, cases_file: Path, rounds: int, name: str, *, benchmark: str = 'reviewbench',
        limit: int | None = None, concurrency: int | None = None, upstream: Path | None = None) -> dict:
    """Score ``build`` over ``rounds`` rounds of the benchmark's dev cases in ``cases_file``."""
    if not NAME.fullmatch(name):
        raise ValueError(f'A name is a plain folder name under {ARTIFACTS}/: {name!r}')
    if limit is not None and limit < 1:
        raise ValueError('Rounds, --limit-cases and --concurrency must be positive')
    return score(build, sets.read(cases_file)[:limit], rounds, ARTIFACTS / name, name, benchmark=benchmark,
                 split='dev', jobs_dir=JOBS, concurrency=concurrency, upstream=upstream)


def score(build: str, cases: list[str] | None, rounds: int, folder: Path, name: str, *, benchmark: str,
          split: str, jobs_dir: Path, concurrency: int | None = None, upstream: Path | None = None) -> dict:
    """Score ``build`` over ``rounds`` rounds of ``cases`` from one split, or of the whole split when None.

    The catalog and summary go in ``folder``, the jobs in ``jobs_dir``. ``run`` passes the dev
    split; only ``loop.confirm`` passes a held-out one, with a folder and jobs under ``confirmations/``.
    """
    if benchmark not in BENCHMARKS:
        raise ValueError(f'Unknown benchmark {benchmark!r}; choose one of {sorted(BENCHMARKS)}')
    bench = BENCHMARKS[benchmark]
    if rounds < 1 or (concurrency is not None and concurrency < 1):
        raise ValueError('Rounds, --limit-cases and --concurrency must be positive')
    check_logins()
    concurrency = concurrency or settings.load()['concurrency']
    path, catalog = folder / 'summary.json', folder / 'catalog'
    if build == 'oracle' and (benchmark != 'reviewbench' or split != 'dev'):
        raise ValueError('The oracle scores ReviewBench dev cases only')
    build = build if build in ('pinned', 'oracle') else str(Path(build).resolve())
    request = dict(build=build, cases=cases)
    if benchmark != 'reviewbench':
        # A ReviewBench summary written before AACR rounds existed still resumes.
        request['benchmark'] = benchmark
    if split != 'dev':
        request['split'] = split
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / 'lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f'Another loop.rounds is scoring {name}') from None
        return _rounds(benchmark, request, rounds, name, path, catalog, concurrency, upstream or bench.upstream,
                       split, jobs_dir)


def _generate(bench, upstream: Path, catalog: Path, request: dict, split: str) -> None:
    """Generate the build's catalog. On failure, remove what this attempt wrote, so the name runs again.

    A held-out set has no case list to drop a case from: when its conversion ran to the end, a
    case that failed (its upstream commit gone) is left out, and catalog.json records it. Only a
    case that fails again, with the same error, when converted once more: a passing failure, a
    network or disk error, would otherwise drop a case from a catalog later rounds share.
    """
    existed = catalog.exists()
    build_file = None if request['build'] in ('pinned', 'oracle') else Path(request['build'])
    if bench is BENCHMARKS['aacr']:
        # AACR prepares each case's repository before it bakes in the build: fail first, as its converter does.
        build.built_wheel(build_file)
    try:
        bench.generate(upstream, catalog, request['cases'], build_file, split)
    except Exception as error:
        if existed:
            raise
        try:
            listing = json.loads((catalog / 'catalog.json').read_text())
        except (OSError, ValueError):
            listing = {}
        failed = {case['task_name']: case.get('error') for case in listing.get('cases', [])
                  if case.get('status') == 'error'}
        if (split != 'dev' and failed and len(listing['cases']) == listing.get('selected_records')
                and len(failed) < len(listing['cases']) and _fails_again(bench, upstream, catalog, failed,
                                                                         build_file, split)):
            return
        shutil.rmtree(catalog, ignore_errors=True)
        if failed:
            raise ValueError(f'These cases failed to convert: {failed}. Drop them from the case list and run '
                             'again; the partial catalog was removed') from error
        raise


def _fails_again(bench, upstream: Path, catalog: Path, failed: dict, build_file: Path | None, split: str) -> bool:
    """Whether every failed case fails again, with the same error, converted on its own once more."""
    retry = Path(tempfile.mkdtemp(prefix='.retry-', dir=catalog.parent))
    try:
        try:
            bench.generate(upstream, retry / 'catalog', sorted(failed), build_file, split)
        except Exception:
            pass
        try:
            again = {case['task_name']: case.get('error')
                     for case in json.loads((retry / 'catalog/catalog.json').read_text()).get('cases', [])
                     if case.get('status') == 'error'}
        except (OSError, ValueError):
            return False
        return again == failed
    finally:
        shutil.rmtree(retry, ignore_errors=True)


def _rounds(benchmark, request, rounds, name, path, catalog, concurrency, upstream, split, jobs_dir) -> dict:
    bench = BENCHMARKS[benchmark]

    def save():
        # The jobs' states first, so a summary that can't be read never loses them.
        _write(path, state)
        done = [entry['job'] for entry in state['jobs'] if entry['state'] == 'finished']
        if done:
            state.update(summarise(catalog, done, benchmark, jobs_dir, oracle=request['build'] == 'oracle'))
            _write(path, state)

    if path.exists():
        state = json.loads(path.read_text())
        if state['request'] != request:
            raise ValueError(f'{name} already scored {state["request"]}; choose a new name')
    else:
        # The converter refuses any case outside the split asked for.
        _generate(bench, upstream, catalog, request, split)
        state = dict(request=request, catalog=str(catalog), jobs=[])
    for entry in state['jobs']:
        if entry['state'] == 'started':
            entry['state'] = outcome(entry['job'], catalog, benchmark, jobs_dir)
    save()
    for number in range(1, rounds + 1):
        if any(entry['round'] == number and entry['state'] == 'finished' for entry in state['jobs']):
            continue
        attempt = 1 + sum(entry['round'] == number for entry in state['jobs'])
        job = f'{name}-round-{number}-attempt-{attempt}'
        if (jobs_dir / job).exists():
            raise FileExistsError(f'Refusing to reopen Harbor job {jobs_dir / job}; choose a new name')
        state['jobs'].append(entry := dict(round=number, job=job, state='started'))
        save()
        config = ORACLE_CONFIG if request['build'] == 'oracle' else bench.config
        subprocess.run([str(HARBOR), 'run', '-c', str(config), '-p', str(catalog), '-o', str(jobs_dir),
                        '-k', '1', '-r', '0', '-n', str(concurrency), '--job-name', job], check=False)
        entry['state'] = outcome(job, catalog, benchmark, jobs_dir)
        save()
        if entry['state'] != 'finished':
            raise RuntimeError(f'Round {number} {entry["state"]} ({job}); fix the cause and run the command '
                               'again for a fresh job')
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--build', required=True,
                        help="'pinned', a candidate's build.json, or 'oracle' for ReviewBench's own true findings")
    parser.add_argument('--cases', required=True, type=Path, help='loop/climb.txt, loop/check.txt or another dev list')
    parser.add_argument('--benchmark', choices=sorted(BENCHMARKS), default='reviewbench',
                        help="ReviewBench, the loop's own scorer, or AACR's dev cases, to check its report and a build")
    parser.add_argument('--rounds', required=True, type=int)
    parser.add_argument('--name', required=True, help='A folder name under artifacts/loop/, and the prefix of its job names')
    parser.add_argument('--limit-cases', type=int, help='Score only the first N cases, for a pilot')
    parser.add_argument('--concurrency', type=int, help="Harbor's -n; defaults to the loop setting")
    arguments = parser.parse_args()
    state = run(arguments.build, arguments.cases, arguments.rounds, arguments.name, benchmark=arguments.benchmark,
                limit=arguments.limit_cases, concurrency=arguments.concurrency)
    print(json.dumps({key: state.get(key) for key in ('side', 'scores')}, indent=2))


if __name__ == '__main__':
    main()
