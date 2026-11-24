"""loop.run with stand-ins for loop.iterate, loop.confirm, the canary, the checks and the clock.

The stand-in iteration appends the entry loop.iterate would, with the verdict and cost the
test plans; the stand-in confirmation gives the planned word, and the stand-in for loop.land
records what it was asked. The ledger, the stop rules and the lock are the real ones.
"""
from datetime import datetime, timedelta, timezone
import fcntl
from functools import partial
import hashlib
import json
import re
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from benchmarks.daydream import build
from loop import confirm as confirmation, land as landing, ledger, rounds, run as looprun, sets, settings

PIN = build.BUILD['source_commit']
DERIVED = dict(sigma=0.02, rounds=2, precision_margin=0.03, wrong_findings_margin=0.2, incomplete_margin=0.5)
STABLE = dict(verdict='stable', job='canary-job', judge='pi/judge', moved=[])
DRIFTED = dict(verdict='drifted', job='canary-job', judge='pi/judge', moved=['case-a'])
LIMIT = "Error: You've hit your usage limit. Upgrade to Pro or try again later."
WORN = (f'reviewbench-test gave 10 verdicts, holdout_max_uses: fresh held-out cases are needed '
        f'({confirmation.FRESH_CASES_ISSUE})')


class Killed(BaseException):
    """The process dying without recording anything."""


@pytest.fixture
def loop(tmp_path, monkeypatch):
    """A repository root with the derived settings set, and stand-ins that follow plans."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(looprun, 'ROOT', tmp_path)
    monkeypatch.setattr(looprun, 'LOCK', tmp_path / 'loop.lock')
    derived = tmp_path / 'settings.toml'
    derived.write_text(settings.FILE.read_text().split('[derived]')[0] + '[derived]\n'
                       + ''.join(f'{key} = {value}\n' for key, value in DERIVED.items()))
    monkeypatch.setattr(settings, 'load', partial(settings.load, derived))
    done = SimpleNamespace(iterations=[], confirms=[], canaries=[], canary_names=[], sleeps=[], prereqs=0,
                           confirmed=[], landed=[], root=tmp_path, clock=None,
                           real=dict(confirm=looprun.confirm, baseline=looprun.baseline))

    def prereqs():
        done.prereqs += 1
    monkeypatch.setattr(looprun, 'prereqs', prereqs)
    monkeypatch.setattr(looprun, 'repository', lambda: 's' * 40)

    def canary_round(name, loaded):
        # Each canary name is stamped fresh; the tests read it without the stamp.
        done.canary_names.append(re.sub(r'-\d{8}T\d{6}$', '', name))
        return done.canaries.pop(0) if done.canaries else STABLE
    monkeypatch.setattr(looprun, 'canary_round', canary_round)

    def baseline(run, name, loaded):
        path = tmp_path / 'artifacts/loop' / (name or f'{run}-baseline') / 'summary.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(side=PIN)))
        return path
    monkeypatch.setattr(looprun, 'baseline', baseline)

    def iterate(run, loaded):
        folder = tmp_path / 'loop-runs' / run
        n = 1 + sum(entry['kind'] == 'iteration' for entry in ledger.read(folder))
        planned = done.iterations.pop(0)
        if planned == 'kill':
            raise Killed
        verdict = dict(accepted='accepted-on-dev', limit='invalid').get(planned, planned)
        reasons = [LIMIT] if planned == 'limit' else [f'{planned} #{n}']
        fields = dict(iteration=n, verdict=dict(verdict=verdict, reasons=reasons),
                      cost=dict(cost_usd_equiv=10, trials=8, reviewer_seconds=3600))
        patch = None
        if verdict != 'invalid':
            fields['candidate'] = dict(commit=f'{n:x}' * 40, patch_id='p' * 40)
            fields['scores'] = dict(climb=dict(gain=dict(grounded_f1=0.01 * n)))
            patch = f'diff {n}\n'
        return ledger.append(folder, 'iteration', fields, patch=patch)
    monkeypatch.setattr(looprun, 'iterate', iterate)

    def confirm(baseline, candidate, loaded):
        assert baseline == PIN
        done.confirmed.append(candidate)
        planned = done.confirms.pop(0)
        if planned == 'kill':
            raise Killed
        return (planned, '') if planned in ledger.CONFIRMATIONS else (None, planned)
    monkeypatch.setattr(looprun, 'confirm', confirm)
    monkeypatch.setattr(landing, 'land', lambda run, n, dry_run, root: done.landed.append((run, n, dry_run, root)))
    monkeypatch.setattr(looprun, 'sleep', done.sleeps.append)
    real_now = looprun.now
    monkeypatch.setattr(looprun, 'now', lambda: done.clock or real_now())

    def run(*arguments, iterations=(), confirms=(), canaries=()):
        done.iterations[:], done.confirms[:], done.canaries[:] = iterations, confirms, canaries
        code = looprun.main(list(arguments))
        assert not done.iterations and not done.confirms and not done.canaries
        return code
    done.run = run
    done.entries = lambda name='run-a': ledger.read(tmp_path / 'loop-runs' / name)
    return done


def kinds(entries):
    return [entry['kind'] if entry['kind'] in ('run-start', 'stop') else
            f'{entry["kind"]} {entry["iteration"]} {entry["verdict"]}' if entry['kind'] == 'confirmation' else
            f'#{entry["iteration"]} {entry["verdict"]["verdict"]}' for entry in entries]


def test_a_run_starts_with_the_effective_settings_and_stops_on_a_plateau(loop, capsys):
    assert loop.run('--run', 'run-a', '--set', 'plateau_iterations=2', '--set', 'auto_open_pr=false',
                    iterations=['rejected', 'invalid']) == 0
    entries = loop.entries()
    assert kinds(entries) == ['run-start', '#1 rejected', '#2 invalid', 'stop']
    start, stop = entries[0], entries[-1]
    assert start['settings'] == looprun.effective(settings.load(plateau_iterations=2, auto_open_pr=False))
    assert start['settings']['plateau_iterations'] == 2 and start['settings']['sigma'] == 0.02
    assert (start['baseline_commit'], start['suite_commit'], start['judge'], start['canary']) == (
        PIN, 's' * 40, 'pi/judge', STABLE)
    assert stop == stop | dict(reason='plateau', detail='2 iterations in a row with nothing accepted on dev '
                                                        '(plateau_iterations)')
    assert loop.prereqs == 1 and loop.canary_names == ['run-a-canary-0']
    out = capsys.readouterr().out
    assert 'Run run-a: 2 iterations' in out and '  #1 rejected: rejected #1' in out and 'Stopped: plateau' in out
    assert 'Best grounded F1 gain on dev: +0.0100 on the climb set, #1' in out
    assert 'Spend: 20.00 USD-equivalent, 16 trials, 2.0 reviewer-hours' in out


@pytest.mark.parametrize('overrides, iterations, detail', [
    (['run_max_iterations=2'], ['rejected', 'rejected'], '2 iterations, run_max_iterations'),
    (['run_max_cost_usd_equiv=15'], ['rejected', 'rejected'], '20.00 USD-equivalent spent, run_max_cost_usd_equiv'),
    (['run_max_wall_hours=0'], [], 'hours since the run started, run_max_wall_hours'),
], ids=['iterations', 'cost', 'wall-hours'])
def test_each_budget_stops_the_run(loop, overrides, iterations, detail):
    arguments = [part for override in overrides for part in ('--set', override)]
    assert loop.run('--run', 'run-a', *arguments, iterations=iterations) == 0
    stop = loop.entries()[-1]
    assert stop['reason'] == 'budget' and detail in stop['detail']
    assert len(loop.entries()) == 2 + len(iterations)


def test_a_winner_is_landed_when_auto_open_pr_is_on_and_stops_the_run_awaiting_merge(loop):
    assert loop.run('--run', 'run-a', iterations=['accepted', 'accepted'],
                    confirms=['not-confirmed', 'confirmed']) == 0
    assert kinds(loop.entries()) == ['run-start', '#1 accepted-on-dev', 'confirmation 1 not-confirmed',
                                     '#2 accepted-on-dev', 'confirmation 2 confirmed', 'stop']
    assert loop.entries()[-1]['reason'] == 'winner-awaiting-merge'
    assert loop.confirmed == ['1' * 40, '2' * 40]
    assert loop.landed == [('run-a', 2, False, loop.root)]


def test_with_auto_open_pr_off_a_winner_is_landed_as_a_dry_run(loop):
    assert loop.run('--run', 'run-a', '--set', 'auto_open_pr=false', iterations=['accepted'],
                    confirms=['confirmed']) == 0
    assert loop.entries()[-1]['reason'] == 'winner-awaiting-merge'
    assert loop.landed == [('run-a', 1, True, loop.root)]


def test_a_landing_that_stops_is_reported_and_the_run_goes_on_to_its_stop(loop, monkeypatch, capsys):
    def stopped(run, n, dry_run, root):
        raise landing.Stop('No signing key')
    monkeypatch.setattr(landing, 'land', stopped)
    assert loop.run('--run', 'run-a', iterations=['accepted'], confirms=['confirmed']) == 0
    assert loop.entries()[-1]['reason'] == 'winner-awaiting-merge'
    assert ('#1 is a winner, but loop.land stopped: No signing key\nLand it by hand: python -m loop.land --run '
            'run-a --iteration 1') in capsys.readouterr().err


def test_a_merged_winner_ends_the_run_since_its_baseline_is_stale(loop):
    assert loop.run('--run', 'run-a', iterations=['accepted'], confirms=['confirmed']) == 0
    url = 'https://github.com/existential-birds/daydream/pull/1'
    folder = loop.root / 'loop-runs/run-a'
    ledger.append(folder, 'landing', dict(iteration=1, url=url, branch='loop/x', commit='1' * 40))
    ledger.append(folder, 'merge', dict(iteration=1, url=url, merge_commit='m' * 40))
    # No more iterations against the old baseline: the merge reaches the next run through the pin bump.
    assert loop.run('--resume', 'run-a', iterations=[]) == 0
    assert loop.entries()[-1]['reason'] == 'winner-merged' and 'loop.land --bump' in loop.entries()[-1]['detail']


def test_the_first_run_on_a_new_pin_reports_its_gap_from_the_merged_winner(loop, monkeypatch, capsys):
    asked = []
    gap = dict(pin='n' * 40, baseline_mean=0.5, baseline_rounds=[0.5], run='run-0', iteration=3, merge_commit='m' * 40,
               winner_mean=0.6, winner_rounds=[0.6], gap=-0.1, margin=0.03, beyond_noise=True)
    monkeypatch.setattr(landing, 'pin_gap', lambda summary, loaded, root: asked.append((summary, root)) or gap)
    assert loop.run('--run', 'run-a', '--set', 'run_max_iterations=0') == 0
    assert asked == [(loop.root / 'artifacts/loop/run-a-baseline/summary.json', loop.root)]
    assert 'a gap of -0.1000, larger than the noise margin 0.0300' in capsys.readouterr().err


@pytest.mark.parametrize('word, reason, kinds_after', [
    ('regressed', 'held-out-regression', ['confirmation 1 regressed']),
    (WORN, 'held-out-worn', []),
    ('Round 2 failed (job); fix the cause', 'infrastructure', []),
], ids=['regressed', 'worn', 'no-verdict'])
def test_a_confirmation_that_regressed_or_was_refused_stops_the_run(loop, word, reason, kinds_after):
    assert loop.run('--run', 'run-a', iterations=['accepted'], confirms=[word]) == 0
    assert kinds(loop.entries()) == ['run-start', '#1 accepted-on-dev', *kinds_after, 'stop']
    assert loop.entries()[-1]['reason'] == reason


def test_the_canary_runs_every_canary_every_iterations_and_its_drift_stops_the_run(loop):
    assert loop.run('--run', 'run-a', '--set', 'canary_every=2', iterations=['rejected'] * 4,
                    canaries=[STABLE, STABLE, DRIFTED]) == 0
    assert kinds(loop.entries())[-2:] == ['#4 rejected', 'stop']
    assert loop.entries()[-1] == loop.entries()[-1] | dict(reason='judge-drift',
                                                           detail='The canary canary-job moved case-a')
    # Named by the ledger's length, so each is a fresh round.
    assert loop.canary_names == ['run-a-canary-0', 'run-a-canary-3', 'run-a-canary-5']


def test_a_drifted_or_unscored_canary_keeps_the_run_from_starting(loop, capsys):
    assert loop.run('--run', 'run-a', canaries=[DRIFTED]) == 1
    assert json.loads(capsys.readouterr().out)['moved'] == ['case-a']
    assert not (loop.root / 'loop-runs/run-a/ledger.jsonl').exists()
    assert loop.run('--run', 'run-a', canaries=[dict(verdict='no-reference', error='No loop/noise.json')]) == 2
    assert 'The run did not start: The canary run-a-canary-0-' in capsys.readouterr().err
    assert not (loop.root / 'loop-runs/run-a/ledger.jsonl').exists()


def test_invalid_iterations_in_a_row_stop_the_run_for_infrastructure(loop):
    assert loop.run('--run', 'run-a', iterations=['invalid', 'rejected', 'invalid', 'invalid', 'invalid']) == 0
    assert loop.entries()[-1] == loop.entries()[-1] | dict(
        reason='infrastructure', detail='3 invalid iterations in a row (invalid_streak_stop)')
    assert loop.sleeps == []


def test_a_subscription_limit_gets_one_wait_and_stops_the_run_when_still_there(loop):
    # Once, then a normal iteration: the wait is spent and the run goes on.
    assert loop.run('--run', 'run-a', '--set', 'run_max_iterations=4',
                    iterations=['limit', 'rejected', 'limit', 'limit']) == 0
    assert loop.sleeps == [3600, 3600]
    stop = loop.entries()[-1]
    assert stop['reason'] == 'infrastructure'
    assert stop['detail'] == ('#4 hit a subscription limit, still there after waiting 60 minutes '
                              '(limit_backoff_minutes)')


def test_a_limit_shows_in_the_proposer_logs_and_the_trial_logs(loop):
    logs = loop.root / '.cache/loop/logs/run-a-1'
    logs.mkdir(parents=True)
    (logs / 'attempt-1.log').write_text('thinking\n')
    assert not looprun.limited(['The proposer exited 1'], ['run-a-1-climb'], logs)
    (logs / 'attempt-2.log').write_text(LIMIT)
    assert looprun.limited(['The proposer exited 1'], [], logs)
    summary = loop.root / 'artifacts/loop/run-a-1-climb/summary.json'
    summary.parent.mkdir(parents=True)
    summary.write_text(json.dumps(dict(jobs=[dict(job='run-a-1-climb-round-1-attempt-1')])))
    trial = loop.root / 'jobs/run-a-1-climb-round-1-attempt-1/case-a__x/agent'
    trial.mkdir(parents=True)
    (trial / 'daydream.stderr.log').write_text(f'codex failed: {LIMIT}\n')
    assert looprun.limited(['RuntimeError: Round 1 failed'], ['run-a-1-climb'])


def test_a_confirmation_limit_gets_one_wait_too(loop):
    assert loop.run('--run', 'run-a', '--set', 'auto_open_pr=false', iterations=['accepted'],
                    confirms=[LIMIT, 'confirmed']) == 0
    assert loop.sleeps == [3600] and loop.entries()[-1]['reason'] == 'winner-awaiting-merge'


def test_resume_after_a_stop_starts_the_streaks_again_and_keeps_the_budgets(loop, capsys):
    assert loop.run('--run', 'run-a', '--set', 'plateau_iterations=2', iterations=['rejected', 'rejected']) == 0
    # The run-start's settings, with what this resumption sets.
    assert loop.run('--resume', 'run-a', '--set', 'run_max_iterations=3', iterations=['rejected']) == 0
    assert kinds(loop.entries()) == ['run-start', '#1 rejected', '#2 rejected', 'stop', '#3 rejected', 'stop']
    assert [entry['reason'] for entry in loop.entries() if entry['kind'] == 'stop'] == ['plateau', 'budget']
    resumes = [json.loads(line) for line in (loop.root / 'loop-runs/run-a/resumes.jsonl').read_text().splitlines()]
    assert [(entry['overrides'], entry['suite_commit']) for entry in resumes] == [({'run_max_iterations': 3},
                                                                                   's' * 40)]
    # The canary ran again before going on.
    assert loop.canary_names == ['run-a-canary-0', 'run-a-canary-4'] and loop.prereqs == 2
    assert 'Run run-a: 3 iterations' in capsys.readouterr().out


def test_resume_after_a_crash_confirms_the_accepted_iteration_it_left(loop):
    with pytest.raises(Killed):
        loop.run('--run', 'run-a', iterations=['accepted'], confirms=['kill'])
    assert kinds(loop.entries()) == ['run-start', '#1 accepted-on-dev']
    # The lock went with the process, and the resumed run confirms #1 before anything else.
    assert loop.run('--resume', 'run-a', '--set', 'plateau_iterations=1',
                    iterations=['rejected'], confirms=['not-confirmed']) == 0
    assert kinds(loop.entries()) == ['run-start', '#1 accepted-on-dev', 'confirmation 1 not-confirmed',
                                     '#2 rejected', 'stop']
    assert loop.confirmed == ['1' * 40, '1' * 40]


def test_a_drifted_canary_at_resumption_is_a_stop(loop):
    with pytest.raises(Killed):
        loop.run('--run', 'run-a', iterations=['rejected', 'kill'])
    assert loop.run('--resume', 'run-a', canaries=[DRIFTED]) == 0
    assert kinds(loop.entries()) == ['run-start', '#1 rejected', 'stop']
    assert loop.entries()[-1]['reason'] == 'judge-drift'


def test_an_error_inside_the_loop_is_an_infrastructure_stop(loop, monkeypatch):
    def broken(run, loaded):
        raise ValueError('Expected one summary in artifacts/loop with the SHA-256 x, found 0')
    monkeypatch.setattr(looprun, 'iterate', broken)
    assert loop.run('--run', 'run-a') == 0
    assert loop.entries()[-1]['detail'] == 'ValueError: Expected one summary in artifacts/loop with the SHA-256 x, found 0'


def test_one_loop_per_machine(loop, capsys):
    with open(looprun.LOCK, 'w') as held:
        held.write('run run-x, process 1\n')
        held.flush()
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert loop.run('--run', 'run-a') == 2
        assert 'Another loop is running on this machine (run run-x, process 1)' in capsys.readouterr().err
    assert not (loop.root / 'loop-runs/run-a').exists() and loop.canary_names == []
    assert loop.run('--run', 'run-a', '--set', 'run_max_iterations=0') == 0


def test_what_keeps_a_run_from_starting_or_resuming(loop, monkeypatch, capsys):
    with pytest.raises(SystemExit):
        looprun.main(['--resume', 'run-a', '--baseline-name', 'base'])
    assert loop.run('--resume', 'run-a') == 2
    assert 'No ledger' in capsys.readouterr().err
    assert loop.run('--run', 'run-a', '--set', 'no_such_setting=1') == 2
    assert loop.run('--run', 'run-a', '--set', 'run_max_iterations=two') == 2
    assert 'wrong type' in capsys.readouterr().err
    assert loop.run('--run', 'run-a', '--set', 'run_max_iterations=0') == 0
    assert loop.run('--run', 'run-a') == 2
    assert 'continue it with --resume run-a' in capsys.readouterr().err

    def dirty():
        raise RuntimeError('Commit or remove these changes first:\n M loop/settings.toml')
    monkeypatch.setattr(looprun, 'repository', dirty)
    assert loop.run('--run', 'run-b') == 2
    assert 'Commit or remove these changes first' in capsys.readouterr().err
    # Without the noise measurement's derived settings, no run starts.
    unmeasured = loop.root / 'unmeasured.toml'
    unmeasured.write_text(settings.FILE.read_text().split('[derived]')[0] + '[derived]\n')
    monkeypatch.setattr(settings, 'load', partial(settings.load.func, unmeasured))
    assert loop.run('--run', 'run-b') == 2
    assert 'unset until the noise measurement derives it' in capsys.readouterr().err
    assert not (loop.root / 'loop-runs/run-b').exists()


def test_settings_on_the_command_line_are_typed_as_toml():
    assert looprun._setting('auto_open_pr=false') == ('auto_open_pr', False)
    assert looprun._setting('run_max_iterations=2') == ('run_max_iterations', 2)
    assert looprun._setting('climb_benchmark=reviewbench') == ('climb_benchmark', 'reviewbench')
    assert looprun._setting('proposer_command=["codex", "exec"]') == ('proposer_command', ['codex', 'exec'])


def test_confirmation_runs_as_its_own_process_and_only_its_word_is_read(loop, monkeypatch):
    calls = []

    def process(command, **options):
        calls.append((command, options['cwd']))
        candidate = command[command.index('--candidate') + 1]
        if candidate == 'b' * 40:
            return SimpleNamespace(returncode=2, stderr=WORN + '\n')
        folder = loop.root / 'confirmations' / candidate
        folder.mkdir(parents=True)
        (folder / 'verdict.json').write_text(json.dumps(dict(verdict='confirmed')))
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(looprun, 'subprocess', SimpleNamespace(run=process))
    confirm, loaded = loop.real['confirm'], settings.load()
    assert confirm(PIN, 'a' * 40, loaded) == ('confirmed', '')
    assert calls == [([sys.executable, '-m', 'loop.confirm', '--baseline', PIN, '--candidate', 'a' * 40,
                       '--concurrency', '2'], loop.root)]
    # A verdict already given is read, not asked for again.
    assert confirm(PIN, 'a' * 40, loaded) == ('confirmed', '') and len(calls) == 1
    assert confirm(PIN, 'b' * 40, loaded) == (None, WORN)


def test_the_run_prunes_between_iterations_through_loop_prune(loop, monkeypatch):
    loaded = settings.load()
    calls = []
    module = SimpleNamespace(prune=lambda run, loaded, **options: calls.append((run, options)) or {})
    monkeypatch.setitem(sys.modules, 'loop.prune', module)
    assert looprun.prune('run-a', loaded) == {} and calls == [('run-a', dict(between_iterations=True,
                                                                             root=loop.root))]


@pytest.fixture
def summaries(loop, monkeypatch):
    """A baseline summary of the pinned build on the climb set, and a stand-in for measuring one."""
    monkeypatch.setattr(looprun, 'baseline', loop.real['baseline'])
    (loop.root / 'climb.txt').write_text('case-a\ncase-b\n')
    monkeypatch.setattr(sets, 'CLIMB', loop.root / 'climb.txt')
    measured = []
    monkeypatch.setattr(rounds, 'run', lambda build, cases, count, name, **options: measured.append(
        (build, cases, count, name, options)))

    def write(name, rounds_done=2, finished=None, side=PIN):
        path = loop.root / 'artifacts/loop' / name / 'summary.json'
        path.parent.mkdir(parents=True)
        jobs = [f'{name}-round-{n}' for n in range(1, rounds_done + 1)]
        path.write_text(json.dumps(dict(request=dict(build='pinned', cases=['case-a', 'case-b']), side=side,
                                        rounds=[dict(job=job) for job in jobs])))
        for job in jobs if finished else ():
            (loop.root / 'jobs' / job).mkdir(parents=True)
            # Harbor writes the local time without a zone.
            (loop.root / 'jobs' / job / 'result.json').write_text(json.dumps(dict(
                finished_at=finished.astimezone().replace(tzinfo=None).isoformat())))
        return path
    return SimpleNamespace(write=write, measured=measured)


def test_a_young_baseline_is_used_and_an_old_or_unfit_one_measured_again(loop, summaries, capsys):
    loaded = settings.load()
    loop.clock = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    young = summaries.write('young', finished=loop.clock - timedelta(days=13))
    assert looprun.baseline('run-a', 'young', loaded) == Path('artifacts/loop/young/summary.json') and young.exists()
    summaries.write('old', finished=loop.clock - timedelta(days=15))
    summaries.write('short', rounds_done=1, finished=loop.clock)
    summaries.write('other', side='o' * 40, finished=loop.clock)
    for name, why in (('old', 'is 15.0 days old, past baseline_max_age_days 14'), ('short', 'has 1 rounds, not 2'),
                      ('other', 'is not the pinned build'), ('missing', 'there is no')):
        assert looprun.baseline('run-a', name, loaded) == Path('artifacts/loop/run-a-baseline/summary.json')
        assert why in capsys.readouterr().err
    assert summaries.measured == [('pinned', sets.CLIMB, 2, 'run-a-baseline', dict(concurrency=2))] * 4
    # Without its jobs, a summary is as old as its file.
    (loop.root / 'jobs').rename(loop.root / 'gone')
    loop.clock = None
    assert looprun.baseline('run-a', 'old', loaded) == Path('artifacts/loop/old/summary.json')


def test_the_run_start_records_the_summary_iterate_finds(loop, summaries):
    summaries.write('young', finished=datetime.now(timezone.utc))
    assert loop.run('--run', 'run-a', '--baseline-name', 'young', '--set', 'run_max_iterations=0') == 0
    assert loop.entries()[0]['baseline_summary_sha256'] == hashlib.sha256(
        (loop.root / 'artifacts/loop/young/summary.json').read_bytes()).hexdigest()
    assert summaries.measured == []


def test_the_runs_spend_counts_the_baselines_check_rounds_an_iteration_made():
    assert looprun.spend(dict(cost_usd_equiv=20, baseline_check=dict(rounds=2, cost_usd_equiv=10))) == 30
    assert looprun.spend(dict(cost_usd_equiv=20)) == 20 and looprun.spend({}) == 0


def test_a_canary_name_is_fresh_each_time_and_a_confirmation_limit_is_found_in_its_logs(tmp_path, monkeypatch):
    assert re.fullmatch(r'run-a-canary-3-\d{8}T\d{6}', looprun.canary_name('run-a', 3))
    monkeypatch.setattr(confirmation, 'CONFIRMATIONS', tmp_path / 'confirmations')
    trial = tmp_path / 'confirmations' / ('c' * 40) / 'reviewbench/jobs/job-1/trial/agent'
    trial.mkdir(parents=True)
    assert not looprun.confirmation_limited('c' * 40, 'Round 1 failed (job-1)')
    (trial / 'daydream.stdout.log').write_text("error: You've hit your usage limit. Try again later.\n")
    assert looprun.confirmation_limited('c' * 40, 'Round 1 failed (job-1)')
