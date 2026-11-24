"""Run one iteration of the loop end to end and record it in the ledger.

    .venv/bin/python -m loop.iterate --run <run>

Run it from the repository root, as ``loop.rounds``. The run is ``loop-runs/<run>/``,
whose ledger starts with its run-start entry. Its baseline is the pinned build:
``baseline_commit`` is the commit in ``benchmarks/daydream/build.json``, and its
climb-set rounds are the ``loop.rounds`` summary ``artifacts/loop/<baseline>/summary.json``
whose SHA-256 is ``baseline_summary_sha256``, with ``rounds`` rounds of the pinned
build on ``loop/climb.txt``. The iteration, numbered after the ledger's last:

1. Proposes a change with ``loop.propose``, from the evidence pack of the baseline's
   climb-set rounds, made once in ``.cache/loop/evidence/<baseline>``.
2. Checks the commit and runs Daydream's gate with ``loop.guard``.
3. Builds the commit as a candidate from ``.cache/loop/daydream``, as
   ``python -m benchmarks.daydream.build .cache/loop/daydream --candidate <commit>`` does.
4. Scores it on the climb set with ``loop.rounds`` as ``<run>-<n>-climb``, one round
   at a time, asking ``loop.decide`` after each: reject ends the iteration, continue
   runs the next round, and the last round gives the verdict.
5. If the climb set accepts, scores the check set the same way as ``<run>-<n>-check``,
   against the baseline's check-set rounds ``<baseline>-check``: the pinned build on
   ``loop/check.txt``, made the first time an iteration needs them and reused after.
6. Appends one iteration entry to the ledger, saving the patch: accepted-on-dev,
   rejected or invalid.
7. Removes the worktree unless the change was accepted. The branch stays in the
   clone, and builds and jobs stay.

An iteration that died without its entry, the machine lost or the process killed, is
redone when the next one starts, from where it got to. Its proposal and candidate are
saved in ``.cache/loop/logs/<run>-<n>/iteration.json`` once proposed, and its build once
built; with its worktree still at that commit, it skips the proposer, and the guard and
build too once built, and ``loop.rounds`` resumes each stage from its last finished round.
Without them, the worktree and branch it left are removed and it is proposed afresh. Its
wall time counts from the resumption; its cost counts every round it scored.

Every way out writes the entry, with as much as the iteration got to. No proposal, a
guard refusal, a failed build, a round ``loop.rounds`` stops on (too many trials
unscored, or the job never finished), too few cases to compare, passing
``iteration_max_wall_hours`` or ``iteration_max_cost_usd_equiv``, an error and an
interrupt are all invalid, with the reason: not a rejection of the idea, which may be
tried again. An interrupt is raised again once recorded. The budget is checked between
steps and after every round, so a round can carry it past by up to its own length; the
cost is the API-equivalent cost of the rounds this iteration ran, counting rounds that
finished before a failure. The baseline's check-set rounds measure the baseline, not
the change: they are reported apart, under ``baseline_check``, and count against neither
budget, though ``loop.run`` counts their cost against the run's. SIGTERM and SIGHUP are
recorded as interrupts.

Nothing here changes the pin or makes the candidate the baseline: that waits for
confirmation (#54) and the loop (#55). Prints the entry, or exits 2 with an ``error``,
recording nothing, when the iteration can't start: no run-start entry or baseline
summary, a derived setting unset, or no login folders.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

from benchmarks.daydream import build
from loop import decide, evidence, guard, ledger, propose, rounds, sets, settings

ROOT = Path(__file__).resolve().parents[1]
CLONE = Path('.cache/loop/daydream')
EVIDENCE = Path('.cache/loop/evidence')
# loop.propose's folders for iteration n, as <run>-<n>.
WORKTREES, LOGS = Path('.cache/loop/worktrees'), Path('.cache/loop/logs')
# What an iteration saves as it goes, so one that dies without its ledger entry resumes.
STATE = 'iteration.json'


class Invalid(Exception):
    """Ends the iteration as invalid; the arguments are its reasons."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def baseline(run: Path, loaded: settings.Settings) -> dict:
    """The run's baseline commit and climb-set summary, refusing what can't be compared."""
    entries = ledger.read(run)
    if not entries:
        raise ValueError(f'No ledger at {run / "ledger.jsonl"}; a run starts with its run-start entry')
    start = entries[0]
    found = [path for path in sorted(rounds.ARTIFACTS.glob('*/summary.json'))
             if _sha256(path) == start['baseline_summary_sha256']]
    if len(found) != 1:
        raise ValueError(f'Expected one summary in {rounds.ARTIFACTS} with the SHA-256 '
                         f'{start["baseline_summary_sha256"]}, found {len(found)}')
    summary = json.loads(found[0].read_text())
    if summary['request'] != dict(build='pinned', cases=sets.read(sets.CLIMB)) or summary.get(
            'side') != start['baseline_commit']:
        raise ValueError(f"{found[0]} is not the pinned build {start['baseline_commit']} on {sets.CLIMB}")
    if len(summary.get('rounds', [])) != loaded['rounds']:
        raise ValueError(f'{found[0]} has {len(summary.get("rounds", []))} rounds, not the {loaded["rounds"]} planned')
    loaded.accept_threshold()
    return dict(commit=start['baseline_commit'], name=found[0].parent.name, summary=found[0],
                iteration=1 + max((entry['iteration'] for entry in entries if entry['kind'] == 'iteration'), default=0))


def _spent(entries: list[dict]) -> dict:
    return dict(reviewer_seconds=sum(entry['elapsed_seconds'] or 0 for entry in entries),
                cost_usd_equiv=sum(entry['cost_usd'] or 0 for entry in entries))


def cost(work: dict) -> dict:
    """What the iteration has spent so far: its rounds' trials, time, cost and tokens, and apart from them the
    baseline's check-set rounds it made."""
    entries = [entry for made in work['rounds'].values() for entry in made]
    return dict(trials=sum(entry['expected'] for entry in entries),
                scored_trials=sum(entry['evaluated'] for entry in entries),
                reviewer_seconds=sum(entry['elapsed_seconds'] or 0 for entry in entries),
                cost_usd_equiv=sum(entry['cost_usd'] or 0 for entry in entries),
                **{key: sum(entry[key] or 0 for entry in entries) for key in rounds.TOKENS},
                rounds={name: len(made) for name, made in work['rounds'].items()},
                wall_seconds=round(time.monotonic() - work['started'] - work['baseline_seconds'], 1),
                proposer_attempts=work['attempts'],
                baseline_check=dict(rounds=len(work['baseline_rounds']), wall_seconds=round(work['baseline_seconds'], 1),
                                    **_spent(work['baseline_rounds'])))


def _budget(work: dict, loaded: settings.Settings) -> None:
    hours = (time.monotonic() - work['started'] - work['baseline_seconds']) / 3600
    spent = cost(work)['cost_usd_equiv']
    if hours > loaded['iteration_max_wall_hours']:
        raise Invalid(f'The iteration ran {hours:.2f} hours, past iteration_max_wall_hours '
                      f'{loaded["iteration_max_wall_hours"]:g}')
    if spent > loaded['iteration_max_cost_usd_equiv']:
        raise Invalid(f'The iteration cost {spent:.2f} USD-equivalent, past iteration_max_cost_usd_equiv '
                      f'{loaded["iteration_max_cost_usd_equiv"]:g}')


def _scored(name: str) -> list[dict]:
    """The rounds a loop.rounds name has scored so far, read back after it stopped."""
    path = rounds.ARTIFACTS / name / 'summary.json'
    return json.loads(path.read_text()).get('rounds', []) if path.exists() else []


def _score(stage: str, against: Path, build_file: Path, side: str, cases: Path, name: str,
           loaded: settings.Settings, work: dict) -> tuple[bool, list[str]]:
    """Score the candidate one round at a time until loop.decide accepts or rejects it."""
    base = json.loads(against.read_text())
    for done in range(1, loaded['rounds'] + 1):
        try:
            state = rounds.run(str(build_file), cases, done, name, concurrency=loaded['concurrency'])
        finally:
            # Counted even when the round stopped: the rounds before it were spent.
            work['rounds'][name] = _scored(name)
        if state['side'] != side:
            raise Invalid(f'{name} scored the build {state["side"]}, not the candidate {side}')
        result = decide.decide(base, state, stage, loaded)
        summary = rounds.ARTIFACTS / name / 'summary.json'
        work['fields'].setdefault('scores', {})[stage] = dict(
            result, summary=str(summary), summary_sha256=_sha256(summary),
            baseline_summary=str(against), baseline_summary_sha256=_sha256(against))
        if result['verdict'] == 'invalid':
            raise Invalid(*result['reasons'])
        if result['verdict'] == 'reject':
            return False, result['reasons']
        _budget(work, loaded)
        if result['verdict'] == 'accept':
            return True, result['reasons']
    raise ValueError(f'loop.decide gave no verdict after {loaded["rounds"]} rounds')


def baseline_check(base: dict, loaded: settings.Settings, work: dict) -> Path:
    """The baseline's check-set summary, scoring the rounds it still lacks."""
    if build.BUILD['source_commit'] != base['commit']:
        raise Invalid(f"The pin is {build.BUILD['source_commit']}, not the run's baseline {base['commit']}, "
                      "so the baseline's check-set rounds can't be made")
    name = f'{base["name"]}-check'
    path = rounds.ARTIFACTS / name / 'summary.json'
    before = {entry['job'] for entry in _scored(name)}
    started = time.monotonic()
    try:
        state = rounds.run('pinned', sets.CHECK, loaded['rounds'], name, concurrency=loaded['concurrency'])
    finally:
        work['baseline_rounds'] = [entry for entry in _scored(name) if entry['job'] not in before]
        work['baseline_seconds'] += time.monotonic() - started
    if state['side'] != base['commit']:
        raise Invalid(f'{path} scored the build {state["side"]}, not the baseline {base["commit"]}')
    return path


def _state(root: Path, run: str, n: int) -> Path:
    return root / LOGS / f'{run}-{n}' / STATE


def _save(work: dict, root: Path, run: str) -> None:
    path = _state(root, run, work['fields']['iteration'])
    path.parent.mkdir(parents=True, exist_ok=True)
    rounds._write(path, {key: work[key] for key in ('fields', 'patch', 'worktree', 'attempts')})


def unfinished(run: str, n: int, root: Path = ROOT) -> dict | None:
    """What iteration n saved before it died without its ledger entry, if its worktree still holds the commit.

    Otherwise the worktree and branch it left, if any, are removed, so the proposer starts it afresh.
    """
    path, worktree, branch = _state(root, run, n), root / WORKTREES / f'{run}-{n}', f'loop/{run}/{n}'
    if path.exists():
        try:
            saved = json.loads(path.read_text())
            candidate = saved['fields']['candidate']
            commit = candidate['commit']
        except (ValueError, KeyError, TypeError):
            # Unreadable: redo the iteration from its proposal rather than trust it.
            saved = commit = None
        if saved and worktree.is_dir() and guard.git(worktree, 'rev-parse', 'HEAD').strip() == commit:
            if not Path(candidate.get('build', '')).is_file():
                # Built again, with the guard first, rather than scored against a build that is gone.
                for key in ('build', 'wheel_sha256'):
                    candidate.pop(key, None)
                saved['fields'].pop('gate', None)
            return saved
        path.unlink()
    clone = root / CLONE
    if (clone / '.git').exists():
        guard.git(clone, 'worktree', 'prune')
        if worktree.exists():
            guard.git(clone, 'worktree', 'remove', '--force', str(worktree))
        if guard.git(clone, 'branch', '--list', branch).strip():
            guard.git(clone, 'branch', '-D', branch)
    return None


def _propose(run: str, base: dict, loaded: settings.Settings, root: Path, work: dict) -> None:
    fields, n = work['fields'], work['fields']['iteration']
    pack = root / EVIDENCE / base['name']
    if not pack.exists():
        evidence.build(base['summary'], pack)
    result = propose.propose(run, base['commit'], pack, loaded, root=root)
    if result['iteration'] != n:
        raise ValueError(f'The proposer made iteration {result["iteration"]}, not {n}')
    work['attempts'], work['worktree'] = result['attempts'], result['worktree']
    if not result['proposal']:
        raise Invalid(result['reason'], *(f'Attempt {refusal["attempt"]}: {refusal["reason"]}'
                                          for refusal in result['refusals']))
    commit = result['commit']
    fields['proposal'] = result['proposal']
    # The patch before the candidate, since the ledger saves a candidate's patch with it.
    work['patch'] = guard.git(Path(result['worktree']), 'diff', '--binary', *guard.DIFF, result['baseline'], commit)
    fields['candidate'] = dict(commit=commit, patch_id=result['patch_id'], baseline=result['baseline'],
                               branch=result['branch'], diff=result['diff'])
    _save(work, root, run)


def _build(run: str, loaded: settings.Settings, root: Path, work: dict) -> None:
    candidate = work['fields']['candidate']
    work['fields']['gate'] = gate = guard.check(Path(work['worktree']), candidate['baseline'], candidate['commit'],
                                                loaded, guard.benchmark_names())
    if not gate['accepted']:
        raise Invalid(*(f'The guard refused it: {refusal["check"]}: {refusal["detail"]}'
                        for refusal in gate['refusals']))
    _budget(work, loaded)
    try:
        build_file = build.build_candidate(root / CLONE, candidate['commit'])
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise Invalid(f'The candidate build failed: {error}') from error
    candidate.update(build=str(build_file), wheel_sha256=json.loads(build_file.read_text())['wheel_sha256'])
    _save(work, root, run)


def _steps(run: str, base: dict, loaded: settings.Settings, root: Path, work: dict) -> tuple[str, list[str]]:
    fields, n = work['fields'], work['fields']['iteration']
    if 'candidate' not in fields:
        _propose(run, base, loaded, root, work)
    _budget(work, loaded)
    if 'build' not in fields['candidate']:
        _build(run, loaded, root, work)
    build_file, commit = Path(fields['candidate']['build']), fields['candidate']['commit']
    accepted, reasons = _score('climb', base['summary'], build_file, commit, sets.CLIMB, f'{run}-{n}-climb',
                               loaded, work)
    if not accepted:
        return 'rejected', reasons
    accepted, reasons = _score('check', baseline_check(base, loaded, work), build_file, commit, sets.CHECK,
                               f'{run}-{n}-check', loaded, work)
    return ('accepted-on-dev' if accepted else 'rejected'), reasons


def iterate(run: str, loaded: settings.Settings, *, root: Path = ROOT) -> dict:
    """Run one iteration of the run and return its ledger entry."""
    folder = root / 'loop-runs' / run
    base = baseline(folder, loaded)
    rounds.check_logins()
    work = dict(fields=dict(iteration=base['iteration']), patch=None, worktree=None, rounds={}, attempts=0,
                started=time.monotonic(), baseline_rounds=[], baseline_seconds=0.0)
    work.update(unfinished(run, base['iteration'], root) or {})
    interrupted = None
    try:
        verdict, reasons = _steps(run, base, loaded, root, work)
    except Invalid as error:
        verdict, reasons = 'invalid', list(error.args)
    except Exception as error:
        verdict, reasons = 'invalid', [f'{type(error).__name__}: {error}']
    except BaseException as error:
        verdict, reasons, interrupted = 'invalid', [f'Interrupted: {type(error).__name__}'], error
    work['fields']['cost'] = cost(work)
    try:
        entry = ledger.append(folder, 'iteration', dict(work['fields'], verdict=dict(verdict=verdict, reasons=reasons)),
                              patch=work['patch'])
    except BaseException as error:
        # Nothing recorded: say so, and let an interrupt still end the process as one.
        print(f'Iteration {base["iteration"]} could not be recorded ({type(error).__name__}: {error}); its '
              f'worktree {work["worktree"]} is left for a person', file=sys.stderr)
        # An interrupt pending since the steps outranks an ordinary error in recording; a stop that came
        # while recording ends the process itself.
        if interrupted is not None and isinstance(error, Exception):
            raise interrupted from error
        raise
    _state(root, run, base['iteration']).unlink(missing_ok=True)
    if work['worktree'] and verdict != 'accepted-on-dev':
        try:
            guard.git(root / CLONE, 'worktree', 'remove', '--force', work['worktree'])
        except RuntimeError as error:
            print(f'Recorded, but could not remove {work["worktree"]}: {error}', file=sys.stderr)
    if interrupted is not None:
        raise interrupted
    return entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run', required=True, type=ledger._run_name, help='The run, a folder name under loop-runs/')
    arguments = parser.parse_args(argv)

    def interrupt(number, frame):
        raise KeyboardInterrupt(signal.Signals(number).name)
    # A stop from outside is recorded as an interrupt, as Ctrl-C is.
    for number in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, interrupt)
    try:
        entry = iterate(arguments.run, settings.load(), root=ROOT)
    except (OSError, LookupError, RuntimeError, ValueError) as error:
        print(json.dumps(dict(error=str(error)), indent=2))
        return 2
    print(json.dumps(entry, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
