"""loop.land on a stand-in Daydream: a local bare origin and the loop's clone of it, with the real Git.

Signing goes through a stand-in ``gpg.program``; pushing goes to the local bare origin, and
``gh`` is always replaced. Nothing reaches a network remote.
"""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from loop import decide, guard, land, ledger, run as looprun, settings
from real_git import commit
from test_loop_decide import BASE, WIN, summary

DERIVED = dict(sigma=0.02, rounds=3, precision_margin=0.03, wrong_findings_margin=0.2, incomplete_margin=0.5)
LOADED = settings.load(**DERIVED)
TEMPLATE = """## Summary

<!-- 1-3 bullet points. -->

-

## Motivation

## Changes

### Added
-

## Test Plan

- [ ]

## Checklist

<!-- Keep every checklist item. -->

- [ ] Repository PR template followed and every required section completed
- [ ] Root and workflow gate `make check` completed successfully
- [ ] Run `$test-audit` on all test files created or updated in this PR (N/A if no test files changed)
- [ ] Documentation updated (if applicable)
- [ ] Semantic Versioning compatibility impact stated in the PR (or N/A with a reason)
- [ ] Notable changes recorded under `CHANGELOG.md`'s `[Unreleased]` in an appropriate Keep a Changelog category (or N/A with a reason)

## Additional Context
"""
CHANGELOG = '# Changelog\n\n## [Unreleased]\n\n## [0.37.0] - 2026-11-23\n\n### Changed\n\n- Earlier.\n'
DESCRIPTION = 'The reviewer restates each handoff\'s open questions before it reviews.'
# Held-out figures and a held-out case that loop.confirm leaves on disk, which no body may show.
HELD_OUT = dict(reviewbench=dict(grounded_f1=0.6123, cases=['held-out-case-7']))
# The real step that leaves the machine, kept for the test that pushes to the local bare origin.
PUSH = land.push
SIGNER = '#!/bin/sh\ncat >/dev/null\nprintf \'[GNUPG:] SIG_CREATED D 1 8 00 0 K\\n\' >&2\n' \
         'printf -- \'-----BEGIN PGP SIGNATURE-----\\n\\nc3R1Yg==\\n-----END PGP SIGNATURE-----\\n\'\n'


def scores():
    """Real loop.decide results, as loop.iterate records them."""
    climb = decide.decide(summary([BASE] * 10), summary([WIN] * 10, cost=2.6), 'climb', LOADED)
    check = decide.decide(summary([BASE] * 10), summary([WIN] * 9 + [BASE]), 'check', LOADED)
    return dict(climb=climb, check=check)


@pytest.fixture
def daydream(git, tmp_path, monkeypatch, request):
    """A Daydream origin, the loop's clone with the scored commit, and a run whose iteration 2 is confirmed,
    with auto_open_pr on unless the test's parameter turns it off."""
    origin, work, root = tmp_path / 'origin.git', tmp_path / 'work', tmp_path / 'suite'
    git(tmp_path, 'init', '-q', '--bare', '-b', 'main', str(origin))
    git(tmp_path, 'init', '-q', '-b', 'main', str(work))
    base = commit(git, work, {'CHANGELOG.md': CHANGELOG, '.github/PULL_REQUEST_TEMPLATE.md': TEMPLATE,
                              'daydream/review.py': 'PROMPT = "review"\n', 'README.md': 'Daydream\n'})
    git(work, 'remote', 'add', 'origin', str(origin))
    git(work, 'push', '-q', 'origin', 'main')
    clone = root / land.CLONE
    git(tmp_path, 'clone', '-q', str(origin), str(clone))
    git(clone, 'checkout', '-q', '-b', 'loop/run-a/2')
    scored = commit(git, clone, {'daydream/review.py': 'PROMPT = "review; restate open questions"\n'})
    git(clone, 'checkout', '-q', '--detach', base)
    signer = tmp_path / 'signer'
    signer.write_text(SIGNER)
    signer.chmod(0o755)
    git(clone, 'config', 'user.signingkey', 'K')
    git(clone, 'config', 'gpg.program', str(signer))

    folder = root / 'loop-runs/run-a'
    effective = {name: LOADED[name] for name in (*settings.SETTINGS, *settings.DERIVED)}
    effective['auto_open_pr'] = getattr(request, 'param', True)
    ledger.append(folder, 'run-start', dict(settings=effective, suite_commit='s' * 40, judge='pi/judge-model',
                                            baseline_commit=base, baseline_summary_sha256='0' * 64, canary={}))
    ledger.append(folder, 'iteration', dict(iteration=1, verdict=dict(verdict='accepted-on-dev', reasons=[])))
    ledger.append(folder, 'confirmation', dict(iteration=1, verdict='not-confirmed'))
    proposal = dict(hypothesis='Restating open questions keeps handoffs from losing findings.',
                    category='handoff_loss', change=dict(pattern='restate-open-questions', description=DESCRIPTION),
                    prediction='Recall rises.', rejection_criterion='No gain in grounded F1.',
                    quality_guardrails=['Precision holds.'], evidence=['labels.jsonl#L4', 'cases/case-1.json'],
                    files=['daydream/review.py'])
    ledger.append(folder, 'iteration', dict(
        iteration=2, verdict=dict(verdict='accepted-on-dev', reasons=['gain']), proposal=proposal,
        candidate=dict(commit=scored, patch_id='p' * 40, baseline=base, branch='loop/run-a/2',
                       diff=dict(files=1, lines=2), build='build.json', wheel_sha256='w' * 64),
        gate=dict(accepted=True, commit=scored, gate=dict(returncode=0, seconds=81.5), refusals=[]),
        scores=scores(), cost=dict(trials=60, cost_usd_equiv=151.25, reviewer_seconds=7200, wall_seconds=10800)),
        patch='diff\n')
    ledger.append(folder, 'confirmation', dict(iteration=2, verdict='confirmed'))
    (root / 'confirmations' / scored).mkdir(parents=True)
    (root / 'confirmations' / scored / 'result.json').write_text(json.dumps(HELD_OUT))

    checked, opened = [], []
    monkeypatch.setattr(guard, 'benchmark_names', lambda: {'held-out-case-7', 'owner/repo'})

    def check(worktree, base, commit, loaded, names):
        checked.append((base, commit))
        return dict(accepted=True, commit=commit, gate=dict(returncode=0, seconds=90.0), refusals=[])
    monkeypatch.setattr(guard, 'check', check)

    def leaves(*arguments):
        raise AssertionError(f'a dry run left the machine: {arguments}')
    monkeypatch.setattr(land, 'push', leaves)
    monkeypatch.setattr(land, 'open_pr', leaves)
    monkeypatch.setattr(land, 'pr_state', leaves)
    monkeypatch.setattr(land, 'ROOT', root)

    return SimpleNamespace(git=git, origin=origin, work=work, root=root, clone=clone, base=base, scored=scored,
                           folder=folder, checked=checked, opened=opened)


def upstream(daydream, files):
    """A commit to Daydream's main after the baseline."""
    commit(daydream.git, daydream.work, files)
    daydream.git(daydream.work, 'push', '-q', 'origin', 'main')
    return daydream.git(daydream.work, 'rev-parse', 'HEAD')


def test_a_dry_run_writes_a_signed_branch_and_a_body_with_the_dev_numbers_and_no_held_out_one(daydream):
    before = (daydream.folder / 'ledger.jsonl').read_bytes()
    result = land.land('run-a', 2, dry_run=True, root=daydream.root)
    git, clone = daydream.git, daydream.clone
    assert result['branch'] == 'loop/restate-open-questions' and result['dry_run'] and result['url'] is None
    assert git(clone, 'rev-parse', result['branch']) == result['commit']
    # Rebased onto the unmoved main, the changelog line added and signed; nothing pushed or recorded.
    assert git(clone, 'rev-parse', f'{result["commit"]}^') == daydream.base
    assert 'gpgsig ' in git(clone, 'cat-file', 'commit', result['commit'])
    assert git(clone, 'show', f'{result["commit"]}:CHANGELOG.md') == (
        f'# Changelog\n\n## [Unreleased]\n\n### Changed\n\n- {DESCRIPTION}\n\n## [0.37.0] - 2026-11-23\n\n'
        '### Changed\n\n- Earlier.').strip()
    assert git(daydream.origin, 'branch', '--list', result['branch']) == ''
    assert (daydream.folder / 'ledger.jsonl').read_bytes() == before and daydream.checked == []

    text = Path(result['body']).read_text()
    entry = ledger.read(daydream.folder)[3]
    climb, check = entry['scores']['climb'], entry['scores']['check']
    for figure in (climb['candidate']['mean']['grounded_f1'], climb['baseline']['rounds'][0]['grounded_f1'],
                   climb['needed_gain'], climb['candidate']['mean']['cost_per_case'],
                   climb['candidate']['mean']['seconds_per_case'], climb['guards']['cost_per_case']['limit']):
        assert f'{figure:.4f}' in text
    for gain in (climb['gain']['grounded_f1'], check['gain']['grounded_f1'], climb['gain']['grounded_recall']):
        assert f'{gain:+.4f}' in text
    assert all(f'| {name} |' in text for name in climb['guards'])
    lines = (daydream.folder / 'ledger.jsonl').read_bytes().split(b'\n')
    for expected in ('Confirmed on the held-out split.', 'restate-open-questions', 'labels.jsonl#L4',
                     'cases/case-1.json', entry['proposal']['hypothesis'], 's' * 40, 'pi/judge-model', 'w' * 64,
                     hashlib.sha256(lines[3]).hexdigest(), f'--candidate {daydream.scored}',
                     'python -m loop.rounds', '151.2500 USD-equivalent', '## Motivation', '## Test Plan'):
        assert expected in text
    assert '0.6123' not in text and 'held-out-case-7' not in text and '<!--' not in text
    # The checklist: answered with evidence, or N/A with a reason, every item kept.
    assert '- [x] Repository PR template followed' in text
    assert '- [x] Root and workflow gate `make check` completed successfully' in text and '81.5 seconds' in text
    assert '(N/A if no test files changed) N/A: no test files changed' in text
    assert '- [ ] Documentation updated (if applicable) (for a person' in text
    assert '- [x] Semantic Versioning' in text and 'a patch release under 0.y.z' in text
    assert '- [x] Notable changes recorded' in text and f'added under `[Unreleased]` → `Changed`: {DESCRIPTION}' in text

    # A second dry run starts the branch again.
    assert land.land('run-a', 2, dry_run=True, root=daydream.root)['branch'] == result['branch']


def test_main_moved_runs_the_guard_and_gate_again_and_the_body_says_so(daydream):
    main = upstream(daydream, {'README.md': 'Daydream, moved on\n'})
    result = land.land('run-a', 2, dry_run=True, root=daydream.root)
    assert daydream.git(daydream.clone, 'rev-parse', f'{result["commit"]}^') == main
    rebased = daydream.checked[0][1]
    assert daydream.checked == [(main, rebased)] and rebased != daydream.scored
    text = Path(result['body']).read_text()
    assert f'The scored commit `{daydream.scored}` and the proposed commit `{result["commit"]}` differ by a rebase' in text
    assert f'clean clone of `{rebased}` in 90.0 seconds' in text


def test_a_conflict_stops_for_a_person_and_leaves_the_scored_commit(daydream, capsys):
    upstream(daydream, {'daydream/review.py': 'PROMPT = "review differently"\n'})
    assert land.main(['--run', 'run-a', '--iteration', '2', '--dry-run']) == 1
    assert 'does not rebase cleanly onto origin/main' in capsys.readouterr().err
    worktree = daydream.root / land.WORKTREES / 'land-run-a-2'
    assert daydream.git(worktree, 'rev-parse', 'HEAD') == daydream.scored
    assert daydream.git(worktree, 'status', '--porcelain') == '' and daydream.checked == []


def test_without_a_signing_key_it_stops_and_says_what_to_set_up(daydream, capsys):
    daydream.git(daydream.clone, 'config', '--unset', 'user.signingkey')
    assert land.main(['--run', 'run-a', '--iteration', '2', '--dry-run']) == 1
    err = capsys.readouterr().err
    assert 'No signing key' in err and 'git config --global user.signingkey' in err
    assert not (daydream.folder / 'land').exists()
    # A key that can't sign stops the same way.
    daydream.git(daydream.clone, 'config', 'user.signingkey', 'K')
    daydream.git(daydream.clone, 'config', 'gpg.program', '/bin/false')
    with pytest.raises(land.Stop, match='Signing the commit failed'):
        land.land('run-a', 2, dry_run=True, root=daydream.root)


@pytest.mark.parametrize('n, why', [(1, 'not confirmed on held-out \\(not-confirmed\\)'),
                                    (3, 'not confirmed on held-out \\(no confirmation\\)')])
def test_an_iteration_not_confirmed_is_never_landed(daydream, n, why, capsys):
    if n == 3:
        ledger.append(daydream.folder, 'iteration', dict(iteration=3, verdict=dict(verdict='accepted-on-dev',
                                                                                   reasons=[])))
    with pytest.raises(ValueError, match=why):
        land.land('run-a', n, dry_run=True, root=daydream.root)
    assert land.main(['--run', 'run-a', '--iteration', str(n)]) == 2
    assert not (daydream.root / land.WORKTREES).exists()


def test_a_changelog_line_naming_a_review_case_stops_for_a_person(daydream, monkeypatch):
    monkeypatch.setattr(guard, 'benchmark_names', lambda: {'open'})
    with pytest.raises(land.Stop, match='would name a benchmark or a review case'):
        land.land('run-a', 2, dry_run=True, root=daydream.root)


def test_landing_pushes_with_hooks_opens_a_draft_and_records_its_url(daydream, monkeypatch):
    url = 'https://github.com/existential-birds/daydream/pull/2001'
    monkeypatch.setattr(land, 'push', PUSH)
    monkeypatch.setattr(land, 'open_pr', lambda worktree, branch, title, body: daydream.opened.append(
        (branch, title, Path(body).read_text())) or url)
    result = land.land('run-a', 2, root=daydream.root)
    assert result['url'] == url and not result['dry_run']
    assert daydream.git(daydream.origin, 'rev-parse', result['branch']) == result['commit']
    assert daydream.opened == [(result['branch'], 'Change', Path(result['body']).read_text())]
    assert ledger.read(daydream.folder)[-1] | dict(time=None, prev_sha256=None) == dict(
        schema_version=1, run='run-a', time=None, prev_sha256=None, kind='landing', iteration=2, url=url,
        branch=result['branch'], commit=result['commit'])
    with pytest.raises(ValueError, match='already landed'):
        land.land('run-a', 2, dry_run=True, root=daydream.root)


@pytest.mark.parametrize('daydream', [False], indirect=True)
def test_with_auto_open_pr_off_a_landing_is_a_dry_run(daydream):
    assert land.land('run-a', 2, root=daydream.root)['dry_run']


def test_merged_records_the_merge_and_clears_the_winner(daydream, monkeypatch):
    url = 'https://github.com/existential-birds/daydream/pull/2001'
    ledger.append(daydream.folder, 'landing', dict(iteration=2, url=url, branch='loop/x', commit=daydream.scored))
    loaded = settings.load(**DERIVED)
    entries = ledger.read(daydream.folder)
    assert looprun.due(entries, loaded, looprun.now())[0] == 'winner-awaiting-merge'
    monkeypatch.setattr(land, 'pr_state', lambda pr: dict(url=url, state='OPEN', merge_commit=None))
    assert land.main(['--merged', '2001']) == 1
    monkeypatch.setattr(land, 'pr_state', lambda pr: dict(url=url, state='MERGED', merge_commit='m' * 40))
    assert land.main(['--merged', url]) == 0
    entries = ledger.read(daydream.folder)
    assert entries[-1] | dict(time=None, prev_sha256=None) == dict(
        schema_version=1, run='run-a', time=None, prev_sha256=None, kind='merge', iteration=2, url=url,
        merge_commit='m' * 40)
    assert looprun.due(entries, loaded, looprun.now()) is None
    assert land.main(['--merged', url]) == 2


def test_bump_prepares_the_pin_change_on_a_branch_and_runs_nothing(git, tmp_path, monkeypatch, capsys):
    root = tmp_path / 'suite'
    git(tmp_path, 'init', '-q', '-b', 'main', str(root))
    pin = json.loads((Path(__file__).parents[1] / land.PIN).read_text())
    template = (Path(__file__).parents[1] / land.SUITE_TEMPLATE).read_text()
    commit(git, root, {str(land.PIN): json.dumps(pin, indent=2) + '\n', str(land.SUITE_TEMPLATE): template})
    origin = tmp_path / 'origin.git'
    git(tmp_path, 'init', '-q', '--bare', '-b', 'main', str(origin))
    git(tmp_path, 'clone', '-q', str(origin), str(root / land.CLONE))
    built = []

    def build_candidate(source, tag, candidates):
        built.append((source, tag))
        path = candidates / ('n' * 40) / 'build.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(dict(source_commit='n' * 40, version='0.38.0', wheel_name='daydream-0.38.0.whl',
                                        wheel_sha256='h' * 64, codex_cli='0.1.0', candidate=True)))
        return path
    monkeypatch.setattr(land.build, 'build_candidate', build_candidate)
    monkeypatch.setattr(land, 'ROOT', root)
    before = (root / land.PIN).read_text()
    assert land.main(['--bump', 'v0.38.0']) == 0
    assert built == [(root / land.CLONE, 'v0.38.0')]
    worktree = root / land.BUMPS / '0.38.0'
    assert json.loads((worktree / land.PIN).read_text()) == dict(
        source_commit='n' * 40, version='0.38.0', wheel_name='daydream-0.38.0.whl', wheel_sha256='h' * 64,
        codex_cli=pin['codex_cli'])
    assert (root / land.PIN).read_text() == before
    assert git(root, 'log', '-1', '--format=%s', 'pin/daydream-0.38.0') == 'Pin Daydream 0.38.0'
    assert git(root, 'diff', '--name-only', 'main', 'pin/daydream-0.38.0') == str(land.PIN)
    body = (root / land.BUMPS / '0.38.0.md').read_text()
    for heading in ('## Summary', '## Added', '## Changed', '## Removed', '## Verification steps'):
        assert heading in body
    assert '- [ ] Ran the relevant documented repository checks' in body and '`make test` — [result]' in body
    err = capsys.readouterr().err
    for step in ('make setup', 'make test', '--task simnaut_emdash_2', 'make quickstart', 'gh pr create --draft'):
        assert step in err


def test_the_first_run_on_a_new_pin_reports_a_gap_beyond_the_noise(daydream, tmp_path):
    url = 'https://github.com/existential-birds/daydream/pull/2001'
    ledger.append(daydream.folder, 'landing', dict(iteration=2, url=url, branch='loop/x', commit=daydream.scored))
    ledger.append(daydream.folder, 'merge', dict(iteration=2, url=url, merge_commit='m' * 40))
    winner = ledger.read(daydream.folder)[3]['scores']['climb']['candidate']['mean']['grounded_f1']
    path = tmp_path / 'summary.json'
    # The new pin scores as the winner did: within the noise.
    path.write_text(json.dumps(summary([WIN] * 10)))
    gap = land.pin_gap(path, LOADED, root=daydream.root, pin='n' * 40)
    assert gap['winner_mean'] == winner and gap['gap'] == pytest.approx(0) and not gap['beyond_noise']
    assert 'within the noise margin' in land.gap_text(gap)
    # Scoring as the old baseline did is a gap larger than the noise.
    path.write_text(json.dumps(summary([BASE] * 10)))
    gap = land.pin_gap(path, LOADED, root=daydream.root, pin='n' * 40)
    assert gap['beyond_noise'] and gap['margin'] == pytest.approx(LOADED.accept_threshold())
    assert f'{gap["gap"]:+.4f}' in land.gap_text(gap) and 'larger than the noise margin' in land.gap_text(gap)
    # Only the first run on the pin: one that already started on it, or on the old pin, compares nothing.
    assert land.pin_gap(path, LOADED, root=daydream.root, pin=daydream.base) is None
