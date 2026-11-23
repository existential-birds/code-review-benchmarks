"""Run the loop: repeat iterations within a budget, and stop for a stated reason.

    .venv/bin/python -m loop.run [--run <run>] [--baseline-name <name>] [--set key=value]...
    .venv/bin/python -m loop.run --resume <run> [--set key=value]...

Run it from the repository root, as ``loop.iterate``. ``--set`` overrides a setting of
``loop/settings.toml`` for this run, typed as TOML (``--set auto_open_pr=false``).

Start: checks Docker and both logins (``make prereqs``), a clean working tree and the
derived settings (#45), then takes the machine's loop lock, so two loops can't run on one
machine. Runs the judge canary (``loop.canary``) as ``<run>-canary-0``: if it drifts, or
gives no verdict, the run doesn't start and nothing goes in the ledger. The baseline's
climb-set rounds are ``--baseline-name``'s ``loop.rounds`` summary, of the pinned build
on ``loop/climb.txt`` with ``rounds`` rounds, while it is younger than
``baseline_max_age_days``; otherwise they are measured again as ``<run>-baseline``. A
summary's age is its newest round's: the ``finished_at`` Harbor wrote in that job's
``result.json``, or the summary's modification time when no job folder is left. Then the
run-start entry, with the effective settings. The first run on a new pin first reports any
gap beyond the noise between its baseline and the last merged winner's dev scores.

Repeat: before each iteration, the stop rules below; every ``canary_every`` iterations,
the canary again; then one iteration with ``loop.iterate``, and ``loop.prune`` between
iterations once it exists (#56). After ``accepted-on-dev``, ``loop.confirm`` runs as a
separate process against the run's baseline, and the loop reads only its word, from its
``verdict.json``, into a confirmation entry: ``confirmed`` is a winner, which ``loop.land``
lands: its draft pull request opens when ``auto_open_pr`` is on, and otherwise its branch
and body are written locally, a dry run; ``not-confirmed`` goes on; ``regressed`` stops. One iteration runs at a time, and every
Harbor job runs with ``-n`` ``concurrency``.

Each stop is a ``stop`` entry, with its reason and a detail:

- ``plateau``: ``plateau_iterations`` iterations in a row with nothing accepted on dev;
- ``budget``: ``run_max_iterations`` iterations, ``run_max_cost_usd_equiv`` spent on them
  (their rounds' API-equivalent cost; the canary's, the run's baseline's and the
  confirmations' aren't counted) or ``run_max_wall_hours`` since the run-start entry;
- ``held-out-regression``: a confirmation said ``regressed``;
- ``judge-drift``: the canary drifted;
- ``winner-awaiting-merge``: ``max_unmerged_winners`` winners in the run with no ``merge``
  entry (``loop.land --merged``);
- ``held-out-worn``: ``loop.confirm`` refused, since its held-out sets' uses are spent (#58);
- ``infrastructure``: ``invalid_streak_stop`` invalid iterations in a row, a subscription
  limit still there after one wait of ``limit_backoff_minutes``, a canary or confirmation
  that gave no verdict, or an error that stopped the loop itself.

A subscription limit is Codex's "You've hit your usage limit" in an invalid iteration's
reasons, its proposer logs or its rounds' trial logs, or in a canary's or confirmation's
error. The loop waits once, then tries again; a limit met again stops it.

Resume: ``--resume`` continues a run from its ledger after a crash or a stop, with the
run-start's settings and any new ``--set``, kept with the suite's commit in
``loop-runs/<run>/resumes.jsonl``. It checks as at the start and runs the canary again,
whose drift is now a stop; confirms an iteration accepted on dev and not yet confirmed;
and ``loop.iterate`` redoes an iteration that has no entry from its last finished round.
The streaks, plateau and invalid iterations, count from the last stop; the budgets and
winners count the whole run, so raise one with ``--set`` to go past it.

At the end it prints the run: each iteration's verdict, the best gain on dev, the spend
and the stop. Exits 0 once the run stopped with a reason, 1 when the canary drifted
before the run started, and 2 when the run couldn't start or resume.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib

from benchmarks.daydream import build
from loop import canary, confirm as confirmation, guard, iterate as iteration, land as landing, ledger, rounds, sets, \
    settings

ROOT = Path(__file__).resolve().parents[1]
# One loop per machine: its Docker and both subscriptions are shared.
# A fixed path, not $TMPDIR, which differs between users and sandboxed shells on one machine.
LOCK = Path('/tmp/code-review-benchmarks-loop.lock')
# What Codex prints when a subscription's limit is reached.
LIMIT = re.compile(r'hit your usage limit', re.IGNORECASE)


class Stop(Exception):
    """Ends the run: a reason from ``ledger.STOPS`` and its detail."""


# The steps below are what the tests stand in for.


def spend(cost: dict) -> float:
    """An iteration's API-equivalent cost, with the baseline's check-set rounds it made, which its own budget
    leaves out."""
    return cost.get('cost_usd_equiv', 0) + cost.get('baseline_check', {}).get('cost_usd_equiv', 0)

def prereqs() -> None:
    """Docker and both logins, as ``make prereqs`` checks them, and the login folders the rounds mount."""
    # The Makefile's defaults, which loop.rounds needs set.
    os.environ.setdefault('REVIEWER_CODEX_AUTH_DIR', os.environ.get('CODEX_HOME') or str(Path.home() / '.codex'))
    os.environ.setdefault('JUDGE_PI_AUTH_DIR', os.environ.get('PI_CODING_AGENT_DIR') or str(Path.home() / '.pi/agent'))
    result = subprocess.run(['make', '--no-print-directory', 'prereqs'], cwd=ROOT, capture_output=True, text=True,
                            check=False)
    if result.returncode:
        raise RuntimeError(f'make prereqs failed: {(result.stdout + result.stderr).strip()}')
    rounds.check_logins()


def repository() -> str:
    """The suite's commit, refusing uncommitted changes, so the run records what it ran."""
    if changes := guard.git(ROOT, 'status', '--porcelain').strip():
        raise RuntimeError(f'Commit or remove these changes first:\n{changes}')
    return guard.git(ROOT, 'rev-parse', 'HEAD').strip()


def canary_round(name: str, loaded: settings.Settings) -> dict:
    """``python -m loop.canary --name <name>``'s result: one oracle round of the canary cases."""
    try:
        block = canary.canary_block(canary.NOISE)
        return canary.compare(block, rounds.run('oracle', canary.CASES, 1, name, concurrency=loaded['concurrency']))
    except canary.NoReference as error:
        return dict(verdict='no-reference', error=str(error))
    except (ValueError, RuntimeError, OSError) as error:
        return dict(verdict='invalid', error=str(error))


def iterate(run: str, loaded: settings.Settings) -> dict:
    return iteration.iterate(run, loaded, root=ROOT)


def confirm(baseline: str, candidate: str, loaded: settings.Settings) -> tuple[str | None, str]:
    """Confirm the candidate in a process of its own and read only its word: (word, '') or (None, why not)."""
    folder = ROOT / confirmation.CONFIRMATIONS
    try:
        # A verdict given before a crash, not yet in the ledger, is read rather than refused again.
        word = confirmation.verdict(candidate, root=folder)
        if word is None:
            result = subprocess.run(
                [sys.executable, '-m', 'loop.confirm', '--baseline', baseline, '--candidate', candidate,
                 '--concurrency', str(loaded['concurrency'])], cwd=ROOT, capture_output=True, text=True, check=False)
            if result.returncode:
                return None, result.stderr.strip() or f'loop.confirm exited {result.returncode}'
            word = confirmation.verdict(candidate, root=folder)
    except ValueError as error:
        return None, str(error)
    return (word, '') if word else (None, 'loop.confirm exited 0 and wrote no verdict')


def prune(run: str, loaded: settings.Settings) -> dict | None:
    """``loop.prune`` between iterations: ``prune(run, loaded, between_iterations=True)``. A failed prune is
    reported and the run goes on."""
    try:
        pruning = importlib.import_module('loop.prune')
    except ImportError:
        return None
    try:
        return pruning.prune(run, loaded, between_iterations=True, root=ROOT)
    except (OSError, RuntimeError, ValueError) as error:
        print(f'loop.prune failed, and the run goes on: {error}', file=sys.stderr)
        return None


def land(run: str, n: int, loaded: settings.Settings) -> dict | None:
    """Land a winner with ``loop.land``: its draft pull request with ``auto_open_pr`` on, otherwise a dry run that
    writes its branch and body locally and pushes nothing. A landing that stops is reported, and the run goes on."""
    try:
        return landing.land(run, n, dry_run=not loaded['auto_open_pr'], root=ROOT)
    except (landing.Stop, OSError, LookupError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f'#{n} is a winner, but loop.land stopped: {error}\nLand it by hand: '
              f'python -m loop.land --run {run} --iteration {n}', file=sys.stderr)
        return None


def pin_gap(summary: Path, loaded: settings.Settings) -> None:
    """On the first run on a new pin, report its baseline against the merged winner's recorded dev scores."""
    try:
        gap = landing.pin_gap(summary, loaded, root=ROOT)
    except (OSError, LookupError, ValueError, StopIteration) as error:
        print(f'Could not compare the new pin with the merged winner: {error}', file=sys.stderr)
        return
    if gap:
        print(landing.gap_text(gap), file=sys.stderr)


def sleep(seconds: float) -> None:
    time.sleep(seconds)


def now() -> datetime:
    return datetime.now(timezone.utc)


# The run itself.

def _derived(loaded: settings.Settings) -> None:
    """Refuse to start before the noise measurement (#45) derived its settings."""
    for name in settings.DERIVED:
        loaded[name]
    loaded.accept_threshold()


def effective(loaded: settings.Settings) -> dict:
    return {name: loaded[name] for name in (*settings.SETTINGS, *settings.DERIVED)}


@contextmanager
def lock(run: str):
    """The machine's loop lock, held until the run stops; the kernel drops it if the process dies."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, 'a+') as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            file.seek(0)
            raise RuntimeError(f'Another loop is running on this machine ({file.read().strip() or LOCK}); '
                               'one at a time') from None
        file.truncate(0)
        file.write(f'run {run}, process {os.getpid()}\n')
        file.flush()
        yield


def _finished_at(summary: dict, path: Path) -> datetime:
    """When the summary's newest round finished, by Harbor's job result, or else the summary's modification time."""
    times = []
    for entry in summary.get('rounds', []):
        try:
            when = datetime.fromisoformat(json.loads((rounds.JOBS / entry['job'] / 'result.json').read_text())
                                          ['finished_at'])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        # Harbor writes the machine's local time without a zone.
        times.append(when.astimezone() if when.tzinfo is None else when)
    return max(times, default=None) or datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)


def _unusable(path: Path, loaded: settings.Settings) -> str | None:
    """Why a baseline summary can't serve this run, or None."""
    if not path.is_file():
        return f'there is no {path}'
    summary = json.loads(path.read_text())
    if summary.get('request') != dict(build='pinned', cases=sets.read(sets.CLIMB)) or summary.get(
            'side') != build.BUILD['source_commit']:
        return f"{path} is not the pinned build {build.BUILD['source_commit']} on {sets.CLIMB}"
    if len(summary.get('rounds', [])) != loaded['rounds']:
        return f'{path} has {len(summary.get("rounds", []))} rounds, not {loaded["rounds"]}'
    age = (now() - _finished_at(summary, path)).total_seconds() / 86400
    if age > loaded['baseline_max_age_days']:
        return f'{path} is {age:.1f} days old, past baseline_max_age_days {loaded["baseline_max_age_days"]:g}'
    return None


def baseline(run: str, name: str | None, loaded: settings.Settings) -> Path:
    """The pinned build's climb-set summary: ``name``'s while it can serve, else measured again."""
    if name:
        path = rounds.ARTIFACTS / name / 'summary.json'
        if not (why := _unusable(path, loaded)):
            return path
        print(f'Measuring the baseline again as {run}-baseline: {why}', file=sys.stderr)
    rounds.run('pinned', sets.CLIMB, loaded['rounds'], f'{run}-baseline', concurrency=loaded['concurrency'])
    return rounds.ARTIFACTS / f'{run}-baseline' / 'summary.json'


def _tail(path: Path, size: int = 1 << 20) -> str:
    with open(path, 'rb') as file:
        file.seek(max(0, file.seek(0, os.SEEK_END) - size))
        return file.read().decode(errors='replace')


def limited(texts: list[str], names: list[str] = (), logs: Path | None = None) -> bool:
    """Whether a subscription limit shows in the texts, the trial logs of these ``loop.rounds`` names, or ``logs``."""
    if any(LIMIT.search(text) for text in texts):
        return True
    files = sorted(logs.glob('*.log')) if logs and logs.is_dir() else []
    for name in names:
        path = rounds.ARTIFACTS / name / 'summary.json'
        jobs = json.loads(path.read_text()).get('jobs', []) if path.is_file() else []
        files += [file for entry in jobs for file in sorted((rounds.JOBS / entry['job']).rglob('*'))
                  if file.suffix in ('.log', '.txt') and file.is_file()]
    return any(LIMIT.search(_tail(file)) for file in files)


def confirmation_limited(commit: str, error: str) -> bool:
    """Whether loop.confirm stopped on a subscription limit. It keeps Harbor's output in its own log and its
    trials under confirmations/, so those are searched for the limit alone; nothing else in them is read."""
    folder = confirmation.CONFIRMATIONS / commit
    files = [folder / 'confirm.log'] + sorted(file for file in folder.glob('*/jobs/**/*')
                                              if file.suffix in ('.log', '.txt') and file.is_file())
    return bool(LIMIT.search(error)) or any(LIMIT.search(_tail(file)) for file in files if file.is_file())


def canary_name(run: str, entries: int) -> str:
    """A fresh loop.rounds name for a canary: stamped, so a resume runs a new round rather than reading an old one."""
    return f'{run}-canary-{entries}-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")}'


def _wait(state: dict, loaded: settings.Settings, what: str) -> None:
    """One wait for a subscription limit to clear; a limit met again right after stops the run."""
    minutes = loaded['limit_backoff_minutes']
    if state['waited']:
        raise Stop('infrastructure', f'{what} hit a subscription limit, still there after waiting {minutes:g} '
                                     'minutes (limit_backoff_minutes)')
    print(f'{what} hit a subscription limit; waiting {minutes:g} minutes, once', file=sys.stderr)
    sleep(minutes * 60)
    state['waited'] = True


def _canary(name: str, loaded: settings.Settings, state: dict) -> dict:
    """The canary's result, stable or drifted, waiting once for a subscription limit."""
    while True:
        result = canary_round(name, loaded)
        if result['verdict'] in ('stable', 'drifted'):
            # The oracle uses only the judge's login, so a stable canary says nothing of the reviewer's limit.
            return result
        if not limited([result.get('error', '')], [name]):
            raise Stop('infrastructure', f'The canary {name} gave no verdict, {result["verdict"]}: '
                                         f'{result.get("error")}')
        _wait(state, loaded, f'The canary {name}')


def _confirm(folder: Path, start: dict, entry: dict, loaded: settings.Settings, state: dict) -> None:
    """Confirm an iteration accepted on dev, record the word, and land a winner or stop on a regression."""
    n, commit = entry['iteration'], entry['candidate']['commit']
    while True:
        word, error = confirm(start['baseline_commit'], commit, loaded)
        if word:
            break
        if confirmation.FRESH_CASES_ISSUE in error:
            raise Stop('held-out-worn', f'#{n} was not confirmed: {error}')
        if not confirmation_limited(commit, error):
            raise Stop('infrastructure', f'loop.confirm gave #{n} no verdict: {error}')
        _wait(state, loaded, f'The confirmation of #{n}')
    state['waited'] = False
    ledger.append(folder, 'confirmation', dict(iteration=n, verdict=word))
    print(f'#{n} {word} on held-out', file=sys.stderr)
    if word == 'regressed':
        raise Stop('held-out-regression', f'#{n} regressed on held-out')
    if word == 'confirmed':
        land(folder.name, n, loaded)


def _streak(verdicts: list[str], counted) -> int:
    count = 0
    for verdict in reversed(verdicts):
        if not counted(verdict):
            break
        count += 1
    return count


def due(entries: list[dict], loaded: settings.Settings, at: datetime) -> tuple[str, str] | None:
    """The first stop rule the ledger meets, as (reason, detail), or None."""
    iterations = [entry for entry in entries if entry['kind'] == 'iteration']
    last_stop = max((number for number, entry in enumerate(entries) if entry['kind'] == 'stop'), default=-1)
    # The streaks start again after a stop, when a person resumed the run.
    recent = [entry['verdict']['verdict'] for entry in entries[last_stop + 1:] if entry['kind'] == 'iteration']
    if (invalid := _streak(recent, lambda verdict: verdict == 'invalid')) >= loaded['invalid_streak_stop']:
        return 'infrastructure', f'{invalid} invalid iterations in a row (invalid_streak_stop)'
    merged = {entry['iteration'] for entry in entries if entry['kind'] == 'merge'}
    winners = [entry['iteration'] for entry in entries
               if entry['kind'] == 'confirmation' and entry['verdict'] == 'confirmed' and entry['iteration'] not in merged]
    if len(winners) >= loaded['max_unmerged_winners']:
        return 'winner-awaiting-merge', (f'{len(winners)} confirmed and not merged, max_unmerged_winners: '
                                         f'{", ".join(f"#{n}" for n in winners)}')
    if (flat := _streak(recent, lambda verdict: verdict != 'accepted-on-dev')) >= loaded['plateau_iterations']:
        return 'plateau', f'{flat} iterations in a row with nothing accepted on dev (plateau_iterations)'
    if len(iterations) >= loaded['run_max_iterations']:
        return 'budget', f'{len(iterations)} iterations, run_max_iterations'
    spent = sum(spend(entry.get('cost', {})) for entry in iterations)
    if spent >= loaded['run_max_cost_usd_equiv']:
        return 'budget', f'{spent:.2f} USD-equivalent spent, run_max_cost_usd_equiv'
    hours = (at - datetime.fromisoformat(entries[0]['time'])).total_seconds() / 3600
    if hours >= loaded['run_max_wall_hours']:
        return 'budget', f'{hours:.2f} hours since the run started, run_max_wall_hours'
    return None


def drive(run: str, loaded: settings.Settings, state: dict, resumed: bool) -> tuple[str, str]:
    """Repeat iterations until a stop rule says why not; return the reason and its detail."""
    folder = ROOT / 'loop-runs' / run
    canaried = None
    if resumed:
        entries = ledger.read(folder)
        result = _canary(canary_name(run, len(entries)), loaded, state)
        if result['verdict'] == 'drifted':
            raise Stop('judge-drift', f'The canary {result["job"]} moved {", ".join(result["moved"])}')
        canaried = sum(entry['kind'] == 'iteration' for entry in entries)
    while True:
        entries = ledger.read(folder)
        iterations = [entry for entry in entries if entry['kind'] == 'iteration']
        confirmed = {entry['iteration'] for entry in entries if entry['kind'] == 'confirmation'}
        if iterations and iterations[-1]['verdict']['verdict'] == 'accepted-on-dev' and (
                iterations[-1]['iteration'] not in confirmed):
            _confirm(folder, entries[0], iterations[-1], loaded, state)
            continue
        if stop := due(entries, loaded, now()):
            return stop
        if iterations and len(iterations) % loaded['canary_every'] == 0 and canaried != len(iterations):
            canaried = len(iterations)
            result = _canary(canary_name(run, len(entries)), loaded, state)
            if result['verdict'] == 'drifted':
                raise Stop('judge-drift', f'The canary {result["job"]} moved {", ".join(result["moved"])}')
        entry = iterate(run, loaded)
        prune(run, loaded)
        n = entry['iteration']
        print(f'#{n} {entry["verdict"]["verdict"]}: {"; ".join(entry["verdict"]["reasons"])}', file=sys.stderr)
        if entry['verdict']['verdict'] == 'invalid' and limited(
                entry['verdict']['reasons'], [f'{run}-{n}-{stage}' for stage in ('climb', 'check')],
                ROOT / iteration.LOGS / f'{run}-{n}'):
            _wait(state, loaded, f'#{n}')
        else:
            state['waited'] = False


def finish(run: str, loaded: settings.Settings, resumed: bool) -> int:
    """Drive the run to a stop, record it and print the run."""
    folder = ROOT / 'loop-runs' / run
    try:
        reason, detail = drive(run, loaded, dict(waited=False), resumed)
    except Stop as stop:
        reason, detail = stop.args
    except (OSError, LookupError, RuntimeError, ValueError) as error:
        reason, detail = 'infrastructure', f'{type(error).__name__}: {error}'
    ledger.append(folder, 'stop', dict(reason=reason, detail=detail))
    print(report(ledger.read(folder)))
    return 0


def report(entries: list[dict]) -> str:
    """The run in a few lines: each iteration's verdict, the best gain on dev, the spend and the stop."""
    start = entries[0]
    iterations = [entry for entry in entries if entry['kind'] == 'iteration']
    held_out = {entry['iteration']: entry['verdict'] for entry in entries if entry['kind'] == 'confirmation'}
    lines = [f'Run {start["run"]}: {len(iterations)} iterations against the pinned build {start["baseline_commit"]}']
    for entry in iterations:
        n, verdict = entry['iteration'], entry['verdict']
        lines.append(f'  #{n} {verdict["verdict"]}' + (f', {held_out[n]} on held-out' if n in held_out else '')
                     + (f': {verdict["reasons"][0]}' if verdict['reasons'] else ''))
    gains = []
    for entry in iterations:
        scores = entry.get('scores', {})
        if 'gain' in scores.get('climb', {}):
            check = scores.get('check', {}).get('gain', {}).get('grounded_f1')
            gains.append((scores['climb']['gain']['grounded_f1'], entry['iteration'], check))
    if gains:
        gain, n, check = max(gains)
        lines.append(f'Best grounded F1 gain on dev: {gain:+.4f} on the climb set, #{n}'
                     + ('' if check is None else f' ({check:+.4f} on the check set)'))
    else:
        lines.append('Best grounded F1 gain on dev: none scored')
    costs = [entry.get('cost', {}) for entry in iterations]
    hours = (datetime.fromisoformat(entries[-1]['time']) - datetime.fromisoformat(start['time'])).total_seconds()
    lines.append(f'Spend: {sum(spend(cost) for cost in costs):.2f} USD-equivalent, '
                 f'{sum(cost.get("trials", 0) for cost in costs)} trials, '
                 f'{sum(cost.get("reviewer_seconds", 0) for cost in costs) / 3600:.1f} reviewer-hours, '
                 f'{hours / 3600:.1f} hours from the run-start to the last entry')
    if entries[-1]['kind'] == 'stop':
        lines.append(f'Stopped: {entries[-1]["reason"]}: {entries[-1].get("detail", "")}')
    else:
        lines.append(f'Not stopped; continue with python -m loop.run --resume {start["run"]}')
    return '\n'.join(lines)


def start(run: str, baseline_name: str | None, overrides: dict) -> int:
    loaded = settings.load(**overrides)
    _derived(loaded)
    prereqs()
    commit = repository()
    folder = ROOT / 'loop-runs' / run
    with lock(run):
        if (folder / 'ledger.jsonl').exists():
            raise ValueError(f'{run} has a ledger already; continue it with --resume {run}, or choose a new name')
        try:
            result = _canary(canary_name(run, 0), loaded, dict(waited=False))
        except Stop as stop:
            raise RuntimeError(f'The run did not start: {stop.args[1]}') from None
        if result['verdict'] == 'drifted':
            print(json.dumps(result, indent=2, allow_nan=False))
            print(f'The judge drifted on {", ".join(result["moved"])}, so the run did not start. A person decides '
                  'whether the judge changed: python -m loop.canary --record', file=sys.stderr)
            return 1
        summary = baseline(run, baseline_name, loaded)
        pin_gap(summary, loaded)
        ledger.append(folder, 'run-start', dict(
            settings=effective(loaded), suite_commit=commit, judge=result['judge'],
            baseline_commit=build.BUILD['source_commit'],
            baseline_summary_sha256=hashlib.sha256(summary.read_bytes()).hexdigest(), canary=result))
        return finish(run, loaded, resumed=False)


def resume(run: str, overrides: dict) -> int:
    folder = ROOT / 'loop-runs' / run
    entries = ledger.read(folder)
    if not entries:
        raise ValueError(f'No ledger at {folder / "ledger.jsonl"}; start a run without --resume')
    loaded = settings.load(**(entries[0]['settings'] | overrides))
    _derived(loaded)
    prereqs()
    commit = repository()
    with lock(run):
        if commit != entries[0]['suite_commit']:
            print(f'The suite is at {commit}, not the {entries[0]["suite_commit"]} the run started at',
                  file=sys.stderr)
        with open(folder / 'resumes.jsonl', 'a') as file:
            file.write(json.dumps(dict(time=now().isoformat(timespec='seconds'), suite_commit=commit,
                                       overrides=overrides), allow_nan=False) + '\n')
        return finish(run, loaded, resumed=True)


def _setting(text: str) -> tuple[str, object]:
    """``key=value``, the value read as TOML when it is TOML (2, 0.5, false, ["a"]) and as text otherwise."""
    key, equals, value = text.partition('=')
    if not equals or not key.strip():
        raise argparse.ArgumentTypeError(f'A setting is key=value: {text!r}')
    try:
        return key.strip(), tomllib.loads(f'value = {value}')['value']
    except tomllib.TOMLDecodeError:
        return key.strip(), value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    which = parser.add_mutually_exclusive_group()
    which.add_argument('--run', type=ledger._run_name, help='A new run, a folder name under loop-runs/ '
                                                            '(default run-<UTC date and time>)')
    which.add_argument('--resume', type=ledger._run_name, metavar='RUN', help='Continue this run from its ledger')
    parser.add_argument('--baseline-name', help="A loop.rounds name holding the pinned build's climb-set rounds")
    parser.add_argument('--set', type=_setting, action='append', default=[], dest='overrides', metavar='KEY=VALUE',
                        help='Override a loop setting for this run; repeatable')
    arguments = parser.parse_args(argv)
    if arguments.resume and arguments.baseline_name:
        parser.error('--baseline-name starts a run; a resumed run keeps its baseline')
    overrides = dict(arguments.overrides)
    try:
        if arguments.resume:
            return resume(arguments.resume, overrides)
        return start(arguments.run or now().strftime('run-%Y%m%d-%H%M%S'), arguments.baseline_name, overrides)
    except (OSError, LookupError, RuntimeError, ValueError) as error:
        print(error, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
