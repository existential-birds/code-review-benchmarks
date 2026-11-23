"""Score one Daydream build over rounds of ReviewBench dev cases with one command.

Harbor pools the trials of one job, so a round is its own job: one pass over the
cases with ``-k 1 -r 0``. This generates one catalog for the build, runs each round
with ``harbor run`` one after another, and summarises them::

    .venv/bin/python -m loop.rounds --build pinned --cases loop/climb.txt --rounds 5 --name baseline

Everything for a name stays in ``artifacts/loop/<name>/``: the catalog and
``summary.json``. The jobs are in Harbor's ``jobs/``. Run again with the same name,
it skips finished rounds and adds rounds up to ``--rounds``. A job that never
finished is never reopened, since resuming can delete trials: it is recorded as
abandoned and its round starts again as a fresh job.

The summary's ``side`` and per-round counts come from ``benchmarks.reviewbench.report``,
with its checks. Each round's scores pool its scored trials with
``benchmarks.reviewbench.metric``; ``mean`` and ``stddev`` (sample, n-1) are over the
rounds that define a score. ``cases`` keeps every case's overall counts in every
round, null where unscored, so two summaries can be compared on the cases both scored.
"""
from __future__ import annotations

import argparse
import json
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
COUNTS = ('expected', 'evaluated', 'failed', 'incomplete', 'completed_empty', 'omitted_findings',
          'elapsed_seconds', 'cost_usd', 'cost_trials')
TOKENS = ('n_input_tokens', 'n_cache_tokens', 'n_output_tokens')


def finished(job: str) -> bool:
    try:
        return json.loads((JOBS / job / 'result.json').read_text()).get('finished_at') is not None
    except (OSError, ValueError):
        return False


def summarise(catalog: Path, jobs: list[str]) -> dict:
    """The side, every round's counts and pooled scores, their spread, and each case's counts."""
    side, = report.report(catalog, [[JOBS / job for job in jobs]])['sides'].items()
    commit, block = side
    rounds, cases = [], {}
    for number, entry in enumerate(block['rounds'], 1):
        rewards = [trial['reward'] for trial in entry['trials']]
        pooled = compute(rewards)
        rounds.append(dict(
            round=number, job=entry['job'], **{key: entry[key] for key in COUNTS},
            **{key: entry['harbor'][key] for key in TOKENS},
            findings_sent=pooled.get('candidate_count', 0), unmatched_judged_wrong=pooled.get('unmatched_fp_count', 0),
            **{key: pooled.get(key) for key in METRICS}))
        for trial in entry['trials']:
            counts = None if trial['reward'] is None else read_counts(trial['reward'])['overall']
            cases.setdefault(trial['task'], [None] * len(jobs))[number - 1] = counts
    spread = {}
    for key in METRICS:
        values = [entry[key] for entry in rounds if entry[key] is not None]
        spread[key] = dict(defined_rounds=len(values), mean=statistics.fmean(values) if values else None,
                           stddev=statistics.stdev(values) if len(values) > 1 else None)
    expected = json.loads((catalog / 'catalog.json').read_text())['tasks']
    return dict(side=commit, rounds=rounds, scores=spread,
                cases={case: cases.get(case, [None] * len(jobs)) for case in expected})


def run(build: str, cases_file: Path, rounds: int, name: str, *, limit: int | None = None,
        concurrency: int | None = None, upstream: Path = UPSTREAM) -> dict:
    folder = ARTIFACTS / name
    path, catalog = folder / 'summary.json', folder / 'catalog'
    cases = sets.read(cases_file)[:limit]
    request = dict(build=build, cases=cases)
    concurrency = concurrency or settings.load()['concurrency']

    def save():
        done = [entry['job'] for entry in state['jobs'] if entry['state'] == 'finished']
        state.update(summarise(catalog, done) if done else {})
        path.write_text(json.dumps(state, indent=2, allow_nan=False) + '\n')

    if path.exists():
        state = json.loads(path.read_text())
        if state['request'] != request:
            raise ValueError(f'{name} already scored {state["request"]}; choose a new name')
    else:
        # The converter refuses any case outside the dev split, and nothing here asks for another.
        convert.generate_catalog(upstream, catalog, split='dev', task_names=cases,
                                 build_file=None if build == 'pinned' else Path(build))
        state = dict(request=request, catalog=str(catalog), jobs=[])
        save()
    for entry in state['jobs']:
        if entry['state'] == 'started':
            entry['state'] = 'finished' if finished(entry['job']) else 'abandoned'
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
        entry['state'] = 'finished' if finished(job) else 'abandoned'
        save()
        if entry['state'] == 'abandoned':
            raise RuntimeError(f'Round {number} did not finish ({job}); run the command again for a fresh job')
    save()
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
