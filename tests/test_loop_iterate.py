"""loop.iterate with stand-ins for the proposer, the guard, the candidate build and harbor run.

The worktree, its commit and the patch are Git's, so the stand-in proposer commits
with the real Git through the conftest's ``git`` fixture. The stand-in harbor run
writes job folders with Harbor's own models, as in test_loop_rounds.py: the pinned
build scores as the baseline, and a candidate's rounds follow a plan.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

from benchmarks.daydream import build
from loop import evidence, guard, iterate, ledger, propose, rounds, sets, settings
from real_git import commit
from test_loop_decide import BASE, WIN
from test_loop_rounds import workspace, write_job  # noqa: F401 (workspace is a fixture)
from test_propose import proposal

PIN = build.BUILD['source_commit']
LIMITS = dict(sigma=0.02, rounds=2, precision_margin=0.03, wrong_findings_margin=0.2, incomplete_margin=0.5)
# What a candidate round's trials score: as well as the baseline, better, or not at all.
REWARDS = {'base': BASE, 'win': WIN, 'fail': None}
REFUSAL = dict(check='benchmark', detail='daydream/config.py:1 names a review case or its repository')


def candidate_folder(root: Path, commit: str) -> Path:
    """A candidate build as ``build --candidate`` leaves it, beside its build.json."""
    folder = root / '.cache/daydream-candidates' / commit
    folder.mkdir(parents=True)
    wheel = folder / 'daydream-0.38.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('daydream-0.38.0.dist-info/METADATA', f'Name: daydream\nVersion: 0.38.0\nSummary: {commit}\n')
    (folder / build.LOCK_NAME).write_text('candidate-dependency==1.0 --hash=sha256:' + '1' * 64 + '\n')
    (folder / 'build.json').write_text(json.dumps({
        'source_commit': commit, 'version': '0.38.0', 'wheel_name': wheel.name,
        'wheel_sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(), 'codex_cli': build.BUILD['codex_cli'],
        'candidate': True}))
    return folder / 'build.json'


@pytest.fixture
def loop(workspace, git, monkeypatch):  # noqa: F811
    """A run whose baseline's climb rounds are scored, and a function running one iteration."""
    root = workspace
    (root / 'check.txt').write_text('# check\ncase-b\ncase-a\n')
    monkeypatch.setattr(sets, 'CLIMB', root / 'cases.txt')
    monkeypatch.setattr(sets, 'CHECK', root / 'check.txt')
    clone = root / iterate.CLONE
    clone.mkdir(parents=True)
    git(clone, 'init', '-q', '-b', 'main')
    base = commit(git, clone, {'daydream/config.py': 'EFFORT = "medium"\n'})
    done = SimpleNamespace(plan=[], jobs=[], packs=[], builds=[], root=root, clone=clone, base=base)

    def harbor(command, check):
        job, catalog = command[command.index('--job-name') + 1], Path(command[command.index('-p') + 1])
        listing = json.loads((catalog / 'catalog.json').read_text())
        side = listing['daydream']['source_commit']
        outcome = 'base' if side == PIN else done.plan.pop(0)
        done.jobs.append(job)
        if outcome == 'interrupt':
            raise KeyboardInterrupt
        write_job(Path('jobs') / job, catalog, {case: REWARDS[outcome] for case in listing['tasks']}, build=side)
    monkeypatch.setattr(rounds, 'subprocess', SimpleNamespace(run=harbor))
    rounds.run('pinned', sets.CLIMB, LIMITS['rounds'], 'base')
    summary = root / 'artifacts/loop/base/summary.json'
    ledger.append(root / 'loop-runs/run-a', 'run-start', dict(
        settings={}, suite_commit='a' * 40, judge='pi', baseline_commit=PIN,
        baseline_summary_sha256=hashlib.sha256(summary.read_bytes()).hexdigest(), canary={}))

    monkeypatch.setattr(evidence, 'build', lambda summary, out: (done.packs.append(summary), out.mkdir(parents=True)))

    def stand_in_proposer(run, baseline, pack, loaded, *, root):
        assert baseline == PIN and pack == root / '.cache/loop/evidence/base'
        n = 1 + sum(entry['kind'] == 'iteration' for entry in ledger.read(root / 'loop-runs' / run))
        branch, worktree = f'loop/{run}/{n}', root / '.cache/loop/worktrees' / f'{run}-{n}'
        git(clone, 'worktree', 'add', '-q', '-b', branch, str(worktree), base)
        result = dict(run=run, iteration=n, baseline=base, branch=branch, worktree=str(worktree), refusals=[])
        if not done.propose:
            return result | dict(proposal=None, commit=None, patch_id=None, diff=None, attempts=3,
                                 refusals=[dict(attempt=1, reason='The proposer made no commit')],
                                 reason='No proposal: all 3 attempts were refused')
        head = commit(git, worktree, {'daydream/config.py': f'EFFORT = "high-{n}"\n'})
        return result | dict(proposal=proposal(), commit=head, patch_id=propose.patch_id(worktree, base, head),
                             diff=dict(files=1, lines=2), attempts=1)
    monkeypatch.setattr(propose, 'propose', stand_in_proposer)
    monkeypatch.setattr(guard, 'benchmark_names', lambda: {'owner/project'})
    monkeypatch.setattr(guard, 'check', lambda worktree, base, head, loaded, names: dict(
        accepted=not done.refusals, base=base, commit=head, files=1, lines=2, refusals=done.refusals,
        gate=None if done.refusals else dict(command='make check', returncode=0, timed_out=False, seconds=80.0)))

    def build_candidate(source, head):
        assert source == clone
        done.builds.append(head)
        if done.build_fails:
            raise subprocess.CalledProcessError(1, ['uv', 'build'])
        return candidate_folder(root, head)
    monkeypatch.setattr(build, 'build_candidate', build_candidate)

    def run(plan=(), *, propose=True, refusals=(), build_fails=False, **overrides):
        done.plan[:], done.jobs[:] = plan, []
        done.propose, done.refusals, done.build_fails = propose, list(refusals), build_fails
        entry = iterate.iterate('run-a', settings.load(**LIMITS | overrides), root=root)
        assert not done.plan
        return entry
    done.run = run
    return done


def worktree(loop, n):
    return loop.root / f'.cache/loop/worktrees/run-a-{n}'


def test_a_change_accepted_on_the_climb_set_is_scored_on_the_check_set_against_the_baselines(loop, git):
    pin = (iterate.ROOT / 'benchmarks/daydream/build.json').read_bytes()
    first = loop.run(['win', 'win', 'base', 'base'])
    # Accepted on the climb set, then no better than the baseline on the check set.
    assert first['verdict'] == dict(verdict='rejected', reasons=['The grounded F1 gain 0 does not exceed 0'])
    assert first['scores']['climb']['verdict'] == 'accept' and first['scores']['check']['rounds']['candidate'] == 2
    # The baseline's check-set rounds were made with the pinned build when first needed.
    assert loop.jobs == ['run-a-1-climb-round-1-attempt-1', 'run-a-1-climb-round-2-attempt-1',
                         'base-check-round-1-attempt-1', 'base-check-round-2-attempt-1',
                         'run-a-1-check-round-1-attempt-1', 'run-a-1-check-round-2-attempt-1']
    check = loop.root / 'artifacts/loop/base-check/summary.json'
    assert first['scores']['check']['baseline_summary'] == str(Path('artifacts/loop/base-check/summary.json'))
    assert json.loads(check.read_text())['request']['cases'] == ['case-b', 'case-a']
    # Six rounds of two trials at 2.5 each, the baseline's two check rounds included.
    assert first['cost']['rounds'] == {'run-a-1-climb': 2, 'base-check': 2, 'run-a-1-check': 2}
    assert first['cost']['cost_usd_equiv'] == 30 and first['cost']['trials'] == 12
    assert not worktree(loop, 1).exists() and git(loop.clone, 'rev-parse', 'loop/run-a/1') == first['candidate']['commit']

    second = loop.run(['win'] * 4)
    assert second['verdict']['verdict'] == 'accepted-on-dev'
    assert [second['scores'][stage]['verdict'] for stage in ('climb', 'check')] == ['accept', 'accept']
    # The baseline's check rounds are reused, and cost this iteration nothing.
    assert loop.jobs == [f'run-a-2-{stage}-round-{n}-attempt-1' for stage in ('climb', 'check') for n in (1, 2)]
    assert second['cost']['rounds'] == {'run-a-2-climb': 2, 'base-check': 0, 'run-a-2-check': 2}
    # Its worktree, build and jobs stay; the evidence pack was built once.
    candidate = second['candidate']
    assert worktree(loop, 2).is_dir() and Path(candidate['build']).is_file() and (loop.root / 'jobs').is_dir()
    assert candidate['wheel_sha256'] == json.loads(Path(candidate['build']).read_text())['wheel_sha256']
    assert loop.packs == [Path('artifacts/loop/base/summary.json')]
    patch = (loop.root / 'loop-runs/run-a/patches/2.patch').read_text()
    assert patch == git(worktree(loop, 2), 'diff', loop.base, candidate['commit']) + '\n'
    assert second['scores']['climb']['summary_sha256'] == hashlib.sha256(
        (loop.root / second['scores']['climb']['summary']).read_bytes()).hexdigest()
    assert second['proposal'] == proposal() and second['gate']['accepted']
    assert [entry['kind'] for entry in ledger.read(loop.root / 'loop-runs/run-a')] == ['run-start', 'iteration',
                                                                                       'iteration']
    # The pin is never touched, and the candidate doesn't become the baseline.
    assert (iterate.ROOT / 'benchmarks/daydream/build.json').read_bytes() == pin and build.BUILD['source_commit'] == PIN


@pytest.mark.parametrize('plan, options, verdict, reason, scored', [
    ([], dict(propose=False), 'invalid', 'No proposal: all 3 attempts were refused', None),
    ([], dict(refusals=[REFUSAL]), 'invalid', f'The guard refused it: benchmark: {REFUSAL["detail"]}', None),
    ([], dict(build_fails=True), 'invalid', 'The candidate build failed: Command', None),
    (['base'], {}, 'rejected', 'After 1 of 2 rounds the grounded F1 gain is 0', 1),
    (['base', 'base'], dict(futility_stop=False), 'rejected', 'The grounded F1 gain 0 does not exceed 0.04', 2),
    # Too few trials scored is a lost login or an outage, not a verdict on the change.
    (['fail'], {}, 'invalid', 'RuntimeError: Round 1 failed', None),
    (['win', 'win'], dict(iteration_max_cost_usd_equiv=7), 'invalid', 'The iteration cost 10.00 USD-equivalent', 2),
    ([], dict(iteration_max_wall_hours=0), 'invalid', 'The iteration ran 0.00 hours, past', None),
], ids=['no-proposal', 'guard-refusal', 'build-failure', 'early-reject', 'full-reject', 'failed-round',
        'over-cost', 'over-time'])
def test_every_other_way_out_records_the_iteration_and_removes_its_worktree(loop, plan, options, verdict, reason,
                                                                            scored):
    entry = loop.run(plan, **options)
    assert entry['verdict']['verdict'] == verdict and entry['verdict']['reasons'][0].startswith(reason)
    assert ledger.read(loop.root / 'loop-runs/run-a')[-1] == entry and entry['iteration'] == 1
    assert not worktree(loop, 1).exists()
    assert ('candidate' in entry) == (loop.root / 'loop-runs/run-a/patches/1.patch').exists() == bool(
        options.get('propose', True))
    assert ('gate' in entry) == ('iteration_max_wall_hours' not in options and 'candidate' in entry)
    assert bool(loop.builds) == ('gate' in entry and not options.get('refusals'))
    # Each planned round ran once, and the verdict saw those that finished.
    assert len(loop.jobs) == len(plan)
    assert entry.get('scores', {}).get('climb', {}).get('rounds', {}).get('candidate') == scored
    assert entry['cost']['proposer_attempts'] == (1 if options.get('propose', True) else 3)


def test_an_interrupt_is_recorded_and_raised_again(loop):
    with pytest.raises(KeyboardInterrupt):
        loop.run(['win', 'interrupt'])
    entry = ledger.read(loop.root / 'loop-runs/run-a')[-1]
    assert entry['verdict'] == dict(verdict='invalid', reasons=['Interrupted: KeyboardInterrupt'])
    assert entry['scores']['climb']['verdict'] == 'continue' and entry['cost']['rounds'] == {'run-a-1-climb': 1}
    assert not worktree(loop, 1).exists()
    # The next iteration is the next number; nothing was lost.
    assert loop.run(['base'])['iteration'] == 2


def test_an_iteration_that_cannot_start_records_nothing(loop, tmp_path, monkeypatch, capsys):
    with pytest.raises(ValueError, match='base/summary.json has 2 rounds, not the 3 planned'):
        loop.run(rounds=3)
    (loop.root / 'cases.txt').write_text('case-a\n')
    with pytest.raises(ValueError, match='is not the pinned build'):
        loop.run()
    assert [entry['kind'] for entry in ledger.read(loop.root / 'loop-runs/run-a')] == ['run-start']
    assert not loop.jobs and not loop.packs and not worktree(loop, 1).exists()
    monkeypatch.setattr(iterate, 'ROOT', tmp_path / 'elsewhere')
    assert iterate.main(['--run', 'run-a']) == 2
    assert 'No ledger' in json.loads(capsys.readouterr().out)['error']


class Killed(BaseException):
    """The process dying, so that the iteration's entry is never written."""


def die_before_the_entry(monkeypatch):
    def killed(*arguments, **options):
        raise Killed
    monkeypatch.setattr(ledger, 'append', killed)


def test_an_iteration_that_died_without_its_entry_is_redone_from_its_last_finished_round(loop, monkeypatch):
    append = ledger.append
    die_before_the_entry(monkeypatch)
    with pytest.raises(Killed):
        loop.run(['win', 'interrupt'])
    monkeypatch.setattr(ledger, 'append', append)
    assert [entry['kind'] for entry in ledger.read(loop.root / 'loop-runs/run-a')] == ['run-start']
    saved = json.loads((loop.root / 'artifacts/loop/run-a-1-climb/summary.json').read_text())
    assert [entry['state'] for entry in saved['jobs']] == ['finished', 'started']
    # The proposal, guard and build are kept; the climb set resumes at round 2 with a fresh job.
    entry = loop.run(['win', 'win', 'win'])
    assert entry['iteration'] == 1 and entry['verdict']['verdict'] == 'accepted-on-dev'
    assert loop.jobs == ['run-a-1-climb-round-2-attempt-2', 'base-check-round-1-attempt-1',
                         'base-check-round-2-attempt-1', 'run-a-1-check-round-1-attempt-1',
                         'run-a-1-check-round-2-attempt-1']
    assert loop.builds == [entry['candidate']['commit']] and entry['proposal'] == proposal()
    assert entry['scores']['climb']['rounds']['candidate'] == 2
    assert entry['cost']['rounds'] == {'run-a-1-climb': 2, 'base-check': 2, 'run-a-1-check': 2}
    assert not (loop.root / '.cache/loop/logs/run-a-1/iteration.json').exists()
    assert (loop.root / 'loop-runs/run-a/patches/1.patch').is_file()


def test_an_iteration_that_died_before_its_proposal_was_saved_is_proposed_afresh(loop, monkeypatch, git):
    append = ledger.append
    die_before_the_entry(monkeypatch)
    with pytest.raises(Killed):
        loop.run(propose=False)
    monkeypatch.setattr(ledger, 'append', append)
    # The worktree and branch it left are removed, so the proposer can make them again.
    assert worktree(loop, 1).is_dir() and not (loop.root / '.cache/loop/logs/run-a-1/iteration.json').exists()
    entry = loop.run(['base'])
    assert entry['iteration'] == 1 and entry['verdict']['verdict'] == 'rejected'
    assert git(loop.clone, 'rev-parse', 'loop/run-a/1') == entry['candidate']['commit']


def test_an_iteration_whose_build_is_gone_is_built_again_before_it_is_scored(loop, monkeypatch):
    append = ledger.append
    die_before_the_entry(monkeypatch)
    with pytest.raises(Killed):
        loop.run(['interrupt'])
    monkeypatch.setattr(ledger, 'append', append)
    commit = loop.builds[0]
    shutil.rmtree(loop.root / '.cache/daydream-candidates' / commit)
    entry = loop.run(['base'])
    assert loop.builds == [commit, commit] and entry['gate']['accepted']
    assert entry['verdict']['verdict'] == 'rejected'
