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
split, judge (ReviewBench) or dataset (AACR), build and reviewer slot, so two summaries can be checked as
comparable; a catalog made before slots (#81) ran Daydream's defaults on Codex. ``cases`` keeps every
case's counts in every round (ReviewBench's overall counts, AACR's four reward counts), null where unscored, so two
summaries can be compared on the cases both scored.

Every catalog is generated from the dev split, so a held-out case is refused; held-out
rounds belong to confirmation alone. ReviewBench is the default and the only benchmark
the loop scores. ``--benchmark aacr`` scores AACR dev cases, to check its report and a
build. A case that fails to convert leaves no catalog behind: drop it and run again.

``--build oracle`` scores ReviewBench's own true findings with
``experiments/reviewbench-oracle.yaml`` instead of a Daydream review, so any spread
between its rounds is the judge's alone (#45). It still needs both login folders, since
the tasks mount both, but its trials record no build, and a trial counts as failed when it
left no score.

The catalog is generated under the reviewer and judge slots in ``loop/settings.toml``. A
Codex reviewer needs ``REVIEWER_CODEX_AUTH_DIR``; a keyed provider needs its key, such as
``OPENROUTER_API_KEY``, where this runs, and ``harbor run`` hands it to the agent phase.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

from benchmarks.aacr import convert as aacr_convert, metric as aacr_metric, report as aacr_report
from benchmarks.daydream import build, rounds as shared
from benchmarks.reviewbench import convert, report
from benchmarks.reviewbench.metric import compute
from benchmarks.reviewbench.upstream.metric import read_counts
from loop import sets, settings, slots

ARTIFACTS = Path('artifacts/loop')
JOBS = Path('jobs')
ORACLE_CONFIG = Path('experiments/reviewbench-oracle.yaml')
HARBOR = Path(sys.executable).with_name('harbor')
METRICS = ('grounded_precision', 'grounded_recall', 'grounded_f1',
           'augmented_precision', 'augmented_recall', 'augmented_f1')
COUNTS = ('expected', 'missing', 'evaluated', 'failed', 'incomplete', 'completed_empty', 'omitted_findings',
          'elapsed_seconds', 'cost_usd', 'cost_trials')
TOKENS = ('n_input_tokens', 'n_cache_tokens', 'n_output_tokens')
CATALOG = ('reviewbench_revision', 'split', 'judge', 'daydream')
# The task containers read the logins from these folders; the Makefile sets them, harbor run alone doesn't.
CODEX_LOGIN, JUDGE_LOGIN = 'REVIEWER_CODEX_AUTH_DIR', 'JUDGE_PI_AUTH_DIR'
# A round with more unscored trials than this is failed, not finished: a lost login or a
# Docker outage, not noise (the stop rule of #45). The command stops; a rerun scores it afresh.
MAX_UNSCORED_FRACTION = 0.2


def finished(job: str) -> bool:
    try:
        return json.loads((JOBS / job / 'result.json').read_text()).get('finished_at') is not None
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
    # Catalogs made before slots (#81) record neither; they ran the judge every catalog used then.
    return {**{key: listing[key] for key in ('aacr_revision', 'dataset_sha256', 'split')},
            'judge': listing.get('judge', 'openai/gpt-6.1-sol'), 'reviewer': slots.recorded_reviewer(listing),
            'daydream': json.loads(built.pop())}


def _reviewbench_identity(catalog: Path, listing: dict, expected: dict) -> dict:
    return {**{key: listing[key] for key in CATALOG}, 'reviewer': slots.recorded_reviewer(listing)}


# What differs between the benchmarks; the rounds, resumption and summary are the same.
BENCHMARKS = {
    'reviewbench': SimpleNamespace(
        report=report, config=Path('experiments/reviewbench.yaml'), upstream=Path('.cache/src/ReviewBench'),
        metrics=METRICS, pool=_reviewbench_pool,
        counts=lambda reward: read_counts(reward)['overall'],
        identity=_reviewbench_identity,
        generate=lambda upstream, catalog, cases, build_file: convert.generate_catalog(
            upstream, catalog, split='dev', task_names=cases, build_file=build_file)),
    'aacr': SimpleNamespace(
        report=aacr_report, config=Path('experiments/aacr-baseline.yaml'), upstream=Path('.cache/src/aacr-bench'),
        metrics=aacr_report.RATES, pool=_aacr_pool,
        counts=lambda reward: {key: reward[key] for key in aacr_metric.COUNTS},
        identity=_aacr_identity,
        generate=lambda upstream, catalog, cases, build_file: aacr_convert.generate_catalog(
            upstream, catalog, daydream=True, split='dev', task_names=cases, build_file=build_file)),
}


def summarise(catalog: Path, jobs: list[str], benchmark: str = 'reviewbench', oracle: bool = False) -> dict:
    """The catalog, side, every round's counts and pooled scores, their spread, and each case's counts."""
    bench = BENCHMARKS[benchmark]
    listing, expected = bench.report.read_catalog(catalog)
    entries = [bench.report.read_round(JOBS / job, expected) for job in jobs]
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


def outcome(job: str, catalog: Path, benchmark: str = 'reviewbench') -> str:
    """finished, failed (too many trials unscored) or abandoned (the job never finished)."""
    if not finished(job):
        return 'abandoned'
    bench = BENCHMARKS[benchmark].report
    entry = bench.read_round(JOBS / job, bench.read_catalog(catalog)[1])
    return 'failed' if entry['expected'] - entry['evaluated'] > MAX_UNSCORED_FRACTION * entry['expected'] else 'finished'


def _write(path: Path, state: dict) -> None:
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, indent=2, allow_nan=False) + '\n')
    os.replace(temporary, path)


def check_credentials(reviewer: dict, oracle: bool = False) -> None:
    """Refuse to start without the judge's login folder and the reviewer slot's login or key.

    A Codex reviewer's tasks mount its login folder, the oracle's too; the oracle runs no
    reviewer, so it needs no key. Only a key's presence is checked; it is never read into a
    file or printed."""
    folders = [JUDGE_LOGIN] + ([CODEX_LOGIN] if reviewer['backend'] == 'codex' else [])
    missing = [name for name in folders if not os.environ.get(name) or not Path(os.environ[name]).is_dir()]
    if missing:
        raise ValueError(f'Set {" and ".join(missing)} to the login folders, as the Makefile does')
    key = slots.reviewer_access(reviewer)['key']
    if key and not oracle and not os.environ.get(key):
        raise ValueError(f'Set {key} for the {reviewer["backend"]} reviewer; harbor run passes it to the agent phase')


def run(build: str, cases_file: Path, rounds: int, name: str, *, benchmark: str = 'reviewbench',
        limit: int | None = None, concurrency: int | None = None, upstream: Path | None = None) -> dict:
    """Score ``build`` over ``rounds`` rounds of the benchmark's dev cases in ``cases_file``."""
    if benchmark not in BENCHMARKS:
        raise ValueError(f'Unknown benchmark {benchmark!r}; choose one of {sorted(BENCHMARKS)}')
    bench = BENCHMARKS[benchmark]
    if rounds < 1 or (limit is not None and limit < 1) or (concurrency is not None and concurrency < 1):
        raise ValueError('Rounds, --limit-cases and --concurrency must be positive')
    loaded = settings.load()
    check_credentials(loaded['reviewer'], oracle=build == 'oracle')
    concurrency = concurrency or loaded['concurrency']
    folder = ARTIFACTS / name
    path, catalog = folder / 'summary.json', folder / 'catalog'
    if build == 'oracle' and benchmark != 'reviewbench':
        raise ValueError('The oracle scores ReviewBench dev cases only')
    build = build if build in ('pinned', 'oracle') else str(Path(build).resolve())
    request = dict(build=build, cases=sets.read(cases_file)[:limit])
    if benchmark != 'reviewbench':
        # A ReviewBench summary written before AACR rounds existed still resumes.
        request['benchmark'] = benchmark
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / 'lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f'Another loop.rounds is scoring {name}') from None
        return _rounds(benchmark, request, rounds, name, path, catalog, concurrency, upstream or bench.upstream)


def _generate(bench, upstream: Path, catalog: Path, request: dict) -> None:
    """Generate the build's dev catalog. On failure, remove what this attempt wrote, so the name runs again."""
    existed = catalog.exists()
    build_file = None if request['build'] in ('pinned', 'oracle') else Path(request['build'])
    if bench is BENCHMARKS['aacr']:
        # AACR prepares each case's repository before it bakes in the build: fail first, as its converter does.
        build.built_wheel(build_file)
    try:
        bench.generate(upstream, catalog, request['cases'], build_file)
    except Exception as error:
        if existed:
            raise
        try:
            listing = json.loads((catalog / 'catalog.json').read_text())
        except (OSError, ValueError):
            listing = {}
        shutil.rmtree(catalog, ignore_errors=True)
        failed = {case['task_name']: case.get('error') for case in listing.get('cases', [])
                  if case.get('status') == 'error'}
        if failed:
            raise ValueError(f'These cases failed to convert: {failed}. Drop them from the case list and run '
                             'again; the partial catalog was removed') from error
        raise


def _rounds(benchmark, request, rounds, name, path, catalog, concurrency, upstream) -> dict:
    bench = BENCHMARKS[benchmark]

    def save():
        # The jobs' states first, so a summary that can't be read never loses them.
        _write(path, state)
        done = [entry['job'] for entry in state['jobs'] if entry['state'] == 'finished']
        if done:
            state.update(summarise(catalog, done, benchmark, oracle=request['build'] == 'oracle'))
            _write(path, state)

    if path.exists():
        state = json.loads(path.read_text())
        if state['request'] != request:
            raise ValueError(f'{name} already scored {state["request"]}; choose a new name')
    else:
        # The converter refuses any case outside the dev split, and nothing here asks for another.
        _generate(bench, upstream, catalog, request)
        state = dict(request=request, catalog=str(catalog), jobs=[])
    for entry in state['jobs']:
        if entry['state'] == 'started':
            entry['state'] = outcome(entry['job'], catalog, benchmark)
    save()
    for number in range(1, rounds + 1):
        if any(entry['round'] == number and entry['state'] == 'finished' for entry in state['jobs']):
            continue
        attempt = 1 + sum(entry['round'] == number for entry in state['jobs'])
        job = f'{name}-round-{number}-attempt-{attempt}'
        if (JOBS / job).exists():
            raise FileExistsError(f'Refusing to reopen Harbor job {JOBS / job}; choose a new name')
        state['jobs'].append(entry := dict(round=number, job=job, state='started'))
        save()
        config = ORACLE_CONFIG if request['build'] == 'oracle' else bench.config
        subprocess.run([str(HARBOR), 'run', '-c', str(config), '-p', str(catalog), '-k', '1', '-r', '0',
                        '-n', str(concurrency), '--job-name', job], check=False)
        entry['state'] = outcome(job, catalog, benchmark)
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
    parser.add_argument('--name', required=True, help='Prefix of the catalog, summary and job names')
    parser.add_argument('--limit-cases', type=int, help='Score only the first N cases, for a pilot')
    parser.add_argument('--concurrency', type=int, help="Harbor's -n; defaults to the loop setting")
    arguments = parser.parse_args()
    state = run(arguments.build, arguments.cases, arguments.rounds, arguments.name, benchmark=arguments.benchmark,
                limit=arguments.limit_cases, concurrency=arguments.concurrency)
    print(json.dumps({key: state.get(key) for key in ('side', 'scores')}, indent=2))


if __name__ == '__main__':
    main()
