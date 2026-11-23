"""Score one Daydream build over rounds of ReviewBench dev cases with one command.

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

The summary's ``side`` and per-round counts come from ``benchmarks.reviewbench.report``'s
reading and checks; a summary whose rounds all failed before any review has no side. Each round's scores pool its scored trials with
``benchmarks.reviewbench.metric``; ``mean`` and ``stddev`` (sample, n-1) are over the
rounds that define a score. ``catalog_identity`` names the ReviewBench revision,
split, judge and build, so two summaries can be checked as comparable. ``cases`` keeps every case's overall counts in every
round, null where unscored, so two summaries can be compared on the cases both scored.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys

from benchmarks.reviewbench import convert, report
from benchmarks.reviewbench.metric import compute
from benchmarks.reviewbench.upstream.metric import read_counts
from loop import sets, settings

ARTIFACTS = Path('artifacts/loop')
JOBS = Path('jobs')
CONFIG = Path('experiments/reviewbench.yaml')
UPSTREAM = Path('.cache/src/ReviewBench')
HARBOR = Path(sys.executable).with_name('harbor')
METRICS = ('grounded_precision', 'grounded_recall', 'grounded_f1',
           'augmented_precision', 'augmented_recall', 'augmented_f1')
COUNTS = ('expected', 'missing', 'evaluated', 'failed', 'incomplete', 'completed_empty', 'omitted_findings',
          'elapsed_seconds', 'cost_usd', 'cost_trials')
TOKENS = ('n_input_tokens', 'n_cache_tokens', 'n_output_tokens')
CATALOG = ('reviewbench_revision', 'split', 'judge', 'daydream')
# The task containers read the logins from these; the Makefile sets them, harbor run alone doesn't.
LOGINS = ('REVIEWER_CODEX_AUTH_DIR', 'JUDGE_PI_AUTH_DIR')
# A round with more unscored trials than this is failed, not finished: a lost login or a
# Docker outage, not noise (the stop rule of #45). The command stops; a rerun scores it afresh.
MAX_UNSCORED_FRACTION = 0.2


def finished(job: str) -> bool:
    try:
        return json.loads((JOBS / job / 'result.json').read_text()).get('finished_at') is not None
    except (OSError, ValueError):
        return False


def summarise(catalog: Path, jobs: list[str]) -> dict:
    """The catalog, side, every round's counts and pooled scores, their spread, and each case's counts."""
    listing, expected = report.read_catalog(catalog)
    entries = [report.read_round(JOBS / job, expected) for job in jobs]
    # Rounds that all failed before any review started record no build.
    sides = report.builds(entries)
    if len(sides) > 1 or None in sides:
        raise ValueError(f'One build per summary, not {sorted(map(str, sides))}')
    rounds, cases = [], {case: [None] * len(jobs) for case in listing['tasks']}
    for number, entry in enumerate(entries, 1):
        pooled = compute([trial['reward'] for trial in entry['trials']])
        scored = pooled['scored_trials'] > 0
        rounds.append(dict(
            round=number, job=entry['job'], **{key: entry[key] for key in COUNTS},
            **{key: entry['harbor'][key] for key in TOKENS},
            findings_sent=pooled.get('candidate_count') if scored else None,
            unmatched_judged_wrong=pooled.get('unmatched_fp_count') if scored else None,
            **{key: pooled.get(key) for key in METRICS}))
        for trial in entry['trials']:
            if trial['reward'] is not None:
                cases[trial['task']][number - 1] = read_counts(trial['reward'])['overall']
    spread = {}
    for key in METRICS:
        values = [entry[key] for entry in rounds if entry[key] is not None]
        spread[key] = dict(defined_rounds=len(values), mean=statistics.fmean(values) if values else None,
                           stddev=statistics.stdev(values) if len(values) > 1 else None)
    return dict(catalog_identity={key: listing[key] for key in CATALOG}, side=next(iter(sides), None),
                rounds=rounds, scores=spread, cases=cases)


def outcome(job: str, catalog: Path) -> str:
    """finished, failed (too many trials unscored) or abandoned (the job never finished)."""
    if not finished(job):
        return 'abandoned'
    entry = report.read_round(JOBS / job, report.read_catalog(catalog)[1])
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


def run(build: str, cases_file: Path, rounds: int, name: str, *, limit: int | None = None,
        concurrency: int | None = None, upstream: Path = UPSTREAM) -> dict:
    if rounds < 1 or (limit is not None and limit < 1) or (concurrency is not None and concurrency < 1):
        raise ValueError('Rounds, --limit-cases and --concurrency must be positive')
    check_logins()
    concurrency = concurrency or settings.load()['concurrency']
    folder = ARTIFACTS / name
    path, catalog = folder / 'summary.json', folder / 'catalog'
    build = build if build == 'pinned' else str(Path(build).resolve())
    request = dict(build=build, cases=sets.read(cases_file)[:limit])
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / 'lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f'Another loop.rounds is scoring {name}') from None
        return _rounds(request, rounds, name, path, catalog, concurrency, upstream)


def _rounds(request, rounds, name, path, catalog, concurrency, upstream) -> dict:
    def save():
        # The jobs' states first, so a summary that can't be read never loses them.
        _write(path, state)
        done = [entry['job'] for entry in state['jobs'] if entry['state'] == 'finished']
        if done:
            state.update(summarise(catalog, done))
            _write(path, state)

    if path.exists():
        state = json.loads(path.read_text())
        if state['request'] != request:
            raise ValueError(f'{name} already scored {state["request"]}; choose a new name')
    else:
        # The converter refuses any case outside the dev split, and nothing here asks for another.
        convert.generate_catalog(upstream, catalog, split='dev', task_names=request['cases'],
                                 build_file=None if request['build'] == 'pinned' else Path(request['build']))
        state = dict(request=request, catalog=str(catalog), jobs=[])
    for entry in state['jobs']:
        if entry['state'] == 'started':
            entry['state'] = outcome(entry['job'], catalog)
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
        subprocess.run([str(HARBOR), 'run', '-c', str(CONFIG), '-p', str(catalog), '-k', '1', '-r', '0',
                        '-n', str(concurrency), '--job-name', job], check=False)
        entry['state'] = outcome(job, catalog)
        save()
        if entry['state'] != 'finished':
            raise RuntimeError(f'Round {number} {entry["state"]} ({job}); fix the cause and run the command '
                               'again for a fresh job')
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--build', required=True, help="'pinned', or a candidate's build.json")
    parser.add_argument('--cases', required=True, type=Path, help='loop/climb.txt, loop/check.txt or another dev list')
    parser.add_argument('--rounds', required=True, type=int)
    parser.add_argument('--name', required=True, help='Prefix of the catalog, summary and job names')
    parser.add_argument('--limit-cases', type=int, help='Score only the first N cases, for a pilot')
    parser.add_argument('--concurrency', type=int, help="Harbor's -n; defaults to the loop setting")
    arguments = parser.parse_args()
    state = run(arguments.build, arguments.cases, arguments.rounds, arguments.name,
                limit=arguments.limit_cases, concurrency=arguments.concurrency)
    print(json.dumps({key: state.get(key) for key in ('side', 'scores')}, indent=2))


if __name__ == '__main__':
    main()
