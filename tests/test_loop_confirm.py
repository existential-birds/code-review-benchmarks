"""loop.confirm over recorded Harbor jobs from a stand-in harbor run. Every number is made up."""
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from benchmarks.aacr import convert as aacr_convert
from benchmarks.daydream import build
from benchmarks.reviewbench import convert
from loop import confirm, decide, ledger, rounds, settings
from test_aacr_report import write_job as write_aacr_job
from test_loop_rounds import argument, counts, write_job
from test_reviewbench import exported_task

PINNED, CANDIDATE, OTHER = build.BUILD['source_commit'], 'c' * 40, 'd' * 40
RB_CASES, AACR_CASES = ('case-t', 'case-u'), ('owner-project-2', 'owner-project-3')


def rb(covered: int, sent: int = 4) -> dict:
    """Two true matched findings of four reference findings, one valid unmatched one, and the rest judged wrong."""
    return counts(sent, 2, 2, 1, 4, covered)


# Each side's rounds: ReviewBench counts and AACR counts (expected, generated, matched, matched references).
BASELINE = dict(reviewbench=[rb(2), rb(2), rb(1)], aacr=[(4, 2, 2, 2), (4, 2, 1, 1), (4, 2, 2, 2)])
GAIN = [rb(3)] * 3


@pytest.fixture
def held_out(tmp_path, monkeypatch, pinned_wheel, candidate_build):
    """A repository root with stand-in sources: ReviewBench's test set is case-t and case-u; AACR holds out
    owner-project-2 to 4, and owner-project-4's commit is gone upstream, so it never converts."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(build, 'CANDIDATES', candidate_build.parents[1])
    exported = {'full': ['case-a', *RB_CASES], 'test': list(RB_CASES)}

    def export(upstream, destination, split, reviewer, judge):
        for name in exported[split]:
            exported_task(destination / name, name)
            (destination / name / 'tests/task.json').write_text(
                json.dumps({'task_id': exported['full'].index(name) + 1, 'evaluation_id': 7}))
        (destination / 'dataset.toml').write_text('')
        (destination / 'metric.py').write_text('')
    monkeypatch.setattr(convert, 'check_source', lambda upstream: None)
    monkeypatch.setattr(convert, 'export', export)

    records = [{'githubPrUrl': f'https://github.com/owner/project/pull/{number}', 'project_main_language': 'Go',
                'source_commit': 'a' * 40, 'target_commit': 'b' * 40,
                'comments': [{'path': 'main.go', 'note': 'verifier only reference', 'from_line': 1, 'to_line': 1}]}
               for number in (1, 2, 3, 4)]

    def write_task(record, output, daydream, build_file, reviewer=None, judge=None):
        task = output / aacr_convert.task_name(record)
        if task.name == 'owner-project-4':
            raise RuntimeError('commit unavailable')
        identity = build.BUILD if build_file is None else json.loads(Path(build_file).read_text())
        (task / 'tests').mkdir(parents=True)
        (task / 'tests/build.json').write_text(json.dumps({'source_commit': identity['source_commit']}))
        return task
    (tmp_path / 'holdout.txt').write_text('# held out\nowner-project-2\nowner-project-3\nowner-project-4\n')
    monkeypatch.setattr(aacr_convert, 'HOLDOUT', tmp_path / 'holdout.txt')
    monkeypatch.setattr(aacr_convert, 'load_records', lambda upstream: records)
    monkeypatch.setattr(aacr_convert, 'write_task', write_task)
    for name in rounds.LOGINS:
        (tmp_path / name).mkdir()
        monkeypatch.setenv(name, str(tmp_path / name))
    return tmp_path


def another_build(commit: str) -> None:
    """A second candidate build of ``commit``, beside the first."""
    folder = build.CANDIDATES / commit
    shutil.copytree(build.CANDIDATES / CANDIDATE, folder)
    identity = json.loads((folder / 'build.json').read_text())
    (folder / 'build.json').write_text(json.dumps({**identity, 'source_commit': commit}))


def harbor(monkeypatch, candidate: dict, *, broken: set = frozenset(), scored=(RB_CASES, AACR_CASES)) -> list[str]:
    """A stand-in harbor run that records each round of the ``scored`` cases as its side scored it. A
    (benchmark, round) in ``broken`` loses every trial of the candidate's first attempt, as a lost login would."""
    jobs = []

    def run(command, check):
        catalog, job = Path(argument(command, '-p')), argument(command, '--job-name')
        side = 'baseline' if 'baselines' in catalog.parts else 'candidate'
        benchmark, number = catalog.parent.name, int(job.split('-round-')[1].split('-')[0])
        commit = PINNED if side == 'baseline' else catalog.parents[1].name
        outcome = (BASELINE if side == 'baseline' else candidate)[benchmark][number - 1]
        lost = side == 'candidate' and (benchmark, number) in broken and job.endswith('attempt-1')
        cases = scored[benchmark == 'aacr']
        trials = {case: None if lost else outcome for case in cases}
        # Harbor's own table goes to the terminal; the command keeps it in its log.
        os.write(1, f'harbor {job} mean 0.123\n'.encode())
        if benchmark == 'reviewbench':
            write_job(Path(argument(command, '-o')) / job, catalog, trials, build=commit)
        else:
            write_aacr_job(Path(argument(command, '-o')) / job, catalog,
                           {case: 'raised' if lost else outcome for case in cases}, commit)
        jobs.append(job)
    monkeypatch.setattr(rounds, 'subprocess', SimpleNamespace(run=run))
    return jobs


@pytest.mark.parametrize('candidate, expected', [
    # A ReviewBench gain, with AACR as before.
    (dict(reviewbench=GAIN, aacr=BASELINE['aacr']), 'confirmed'),
    # The same gain, but AACR's balanced score falls to nothing.
    (dict(reviewbench=GAIN, aacr=[(4, 4, 0, 0)] * 3), 'regressed'),
    # The gain comes with twice the findings, so augmented precision falls from 3/4 to 3/8.
    (dict(reviewbench=[rb(3, sent=8)] * 3, aacr=BASELINE['aacr']), 'not-confirmed'),
    # No gain on ReviewBench, and no loss anywhere.
    (BASELINE, 'not-confirmed'),
])
def test_each_verdict_from_recorded_rounds_of_both_held_out_sets(held_out, monkeypatch, capfd, candidate, expected):
    jobs = harbor(monkeypatch, candidate)
    assert confirm.main(['--baseline', 'pinned', '--candidate', CANDIDATE]) == 0
    out, err = capfd.readouterr()
    assert (out, err) == (expected + '\n', '')
    assert len(jobs) == 12 and all(job.endswith('attempt-1') for job in jobs)
    here = held_out / 'confirmations' / CANDIDATE
    # Everything the command and Harbor printed is in its log, never on stdout.
    assert 'mean 0.123' in (here / 'confirm.log').read_text()
    # verdict.json holds the word and nothing else; the loop reads it through verdict().
    assert json.loads((here / 'verdict.json').read_text()) == {'verdict': expected}
    assert confirm.verdict(CANDIDATE) == expected
    result = json.loads((here / 'result.json').read_text())
    assert result['verdict'] == expected and result['baseline'] == PINNED and result['counted']
    # AACR's case whose commit is gone never converted, on either side, so both compare on the other two.
    assert {name: entry['matched'] for name, entry in result['benchmarks'].items()} == {
        'reviewbench': {'cases': 2, 'of': 2}, 'aacr': {'cases': 2, 'of': 2}}
    f1 = result['benchmarks']['reviewbench']['rule']['grounded_f1']
    assert f1['baseline'] == pytest.approx((2 / 3 + 2 / 3 + 2 / 5) / 3)
    assert f1['margin'] == pytest.approx(2 * (f1['stddev']['baseline'] ** 2 / 3
                                              + f1['stddev']['candidate'] ** 2 / 3) ** .5)
    # Baseline rounds are kept per baseline commit, and every job stays under confirmations/.
    for benchmark, split in (('reviewbench', 'test'), ('aacr', 'holdout')):
        summary = json.loads((held_out / f'confirmations/baselines/{PINNED}/{benchmark}/summary.json').read_text())
        assert summary['catalog_identity']['split'] == split and len(summary['rounds']) == 3
        assert len(list((here / benchmark / 'jobs').iterdir())) == 3
    assert not (held_out / 'jobs').exists() and not (held_out / 'artifacts').exists()
    assert json.loads((held_out / 'confirmations/uses.json').read_text()) == {'reviewbench-test': 2, 'aacr-holdout': 1}


def figure(baseline, candidate, margin):
    return dict(baseline=baseline, candidate=candidate, margin=margin)


@pytest.mark.parametrize('reviewbench, aacr, expected', [
    # Within the margins everywhere, and a ReviewBench gain.
    ((figure(.5, .51, .05), figure(.6, .56, .05)), (figure(.5, .46, .05), figure(.6, .56, .05)), 'confirmed'),
    # A ReviewBench gain of exactly nothing is no gain.
    ((figure(.5, .5, .05), figure(.6, .6, .05)), (figure(.5, .5, .05), figure(.6, .6, .05)), 'not-confirmed'),
    # AACR's match rate falls beyond its margin while its balanced score holds.
    ((figure(.5, .6, .05), figure(.6, .6, .05)), (figure(.5, .5, .05), figure(.6, .5, .05)), 'not-confirmed'),
    # ReviewBench's grounded F1 falls beyond its margin.
    ((figure(.5, .44, .05), figure(.6, .6, .05)), (figure(.5, .6, .05), figure(.6, .6, .05)), 'regressed'),
])
def test_the_rule_reads_each_figure_against_its_margin(reviewbench, aacr, expected):
    comparisons = dict(reviewbench=dict(rule=dict(zip(confirm.READS['reviewbench'], reviewbench))),
                       aacr=dict(rule=dict(zip(confirm.READS['aacr'], aacr))))
    assert confirm.rule(comparisons) == expected


def test_a_failed_round_gives_no_verdict_and_the_rerun_resumes_against_the_same_baseline(held_out, monkeypatch,
                                                                                       capfd):
    jobs = harbor(monkeypatch, dict(reviewbench=GAIN, aacr=BASELINE['aacr']), broken={('aacr', 2)})
    arguments = ['--baseline', 'pinned', '--candidate', CANDIDATE]
    assert confirm.main(arguments) == 2
    out, err = capfd.readouterr()
    assert out == '' and 'Round 2 failed (candidate-aacr-round-2-attempt-1)' in err
    here = held_out / 'confirmations' / CANDIDATE
    assert not (here / 'verdict.json').exists() and not (here / 'result.json').exists()
    assert not (held_out / 'confirmations/uses.json').exists() and confirm.verdict(CANDIDATE) is None
    # Started against the pinned build, it keeps that baseline.
    another_build(OTHER)
    with pytest.raises(ValueError, match='a confirmation keeps its baseline'):
        confirm.confirm(OTHER, CANDIDATE)
    finished = len(jobs)
    assert confirm.main(arguments) == 0 and capfd.readouterr().out == 'confirmed\n'
    # Only the failed round ran again, as a fresh job.
    assert jobs[finished:] == ['candidate-aacr-round-2-attempt-2', 'candidate-aacr-round-3-attempt-1']
    assert json.loads((held_out / 'confirmations/uses.json').read_text()) == {'reviewbench-test': 2, 'aacr-holdout': 1}


def test_one_verdict_per_candidate_and_none_once_a_set_is_used_up(held_out, monkeypatch, capfd):
    jobs = harbor(monkeypatch, BASELINE)
    (held_out / 'confirmations').mkdir()
    (held_out / 'confirmations/uses.json').write_text(json.dumps({'reviewbench-test': 9, 'aacr-holdout': 8}))
    assert confirm.confirm('pinned', CANDIDATE) == 'not-confirmed'
    assert json.loads((held_out / 'confirmations/uses.json').read_text()) == {'reviewbench-test': 10,
                                                                             'aacr-holdout': 9}
    # No retry after not-confirmed, whatever the baseline.
    ran = len(jobs)
    another_build(OTHER)
    for baseline in ('pinned', OTHER):
        assert confirm.main(['--baseline', baseline, '--candidate', CANDIDATE]) == 2
        assert 'already has a verdict' in capfd.readouterr().err
    # ReviewBench's test set has given holdout_max_uses verdicts, so a new candidate is refused.
    assert confirm.main(['--baseline', 'pinned', '--candidate', OTHER]) == 2
    assert f'reviewbench-test gave 10 verdicts, holdout_max_uses: fresh held-out cases are needed ' \
           f'({confirm.FRESH_CASES_ISSUE})' in capfd.readouterr().err
    assert len(jobs) == ran and confirm.verdict(OTHER) is None
    # A verdict.json holding anything but the word is refused rather than read.
    (held_out / 'confirmations' / CANDIDATE / 'verdict.json').write_text(
        json.dumps({'verdict': 'not-confirmed', 'grounded_f1': .5}))
    with pytest.raises(ValueError, match='and nothing else'):
        confirm.verdict(CANDIDATE)


def test_a_pilot_scores_the_pinned_build_against_itself_on_the_first_cases_and_stays_out_of_the_count(
        held_out, monkeypatch, capfd):
    harbor(monkeypatch, BASELINE, scored=(['case-t'], ['owner-project-2']))
    assert confirm.main(['--baseline', 'pinned', '--candidate', PINNED, '--limit-cases', '1']) == 0
    assert capfd.readouterr().out == 'not-confirmed\n'
    pilot = held_out / 'confirmations/pilots/1'
    assert json.loads((pilot / PINNED / 'verdict.json').read_text()) == {'verdict': 'not-confirmed'}
    assert not json.loads((pilot / PINNED / 'result.json').read_text())['counted']
    for benchmark, case in (('reviewbench', 'case-t'), ('aacr', 'owner-project-2')):
        assert json.loads((pilot / 'baselines' / PINNED / benchmark / 'summary.json').read_text())['request'][
            'cases'] == [case]
    # The loop never sees it, and nothing was counted.
    assert confirm.verdict(PINNED) is None and not (held_out / 'confirmations/uses.json').exists()


@pytest.mark.parametrize('baseline, candidate, limit, message', [
    # A pilot of a candidate against the baseline would be a held-out verdict nobody counts.
    ('pinned', CANDIDATE, 1, 'the candidate is not the baseline'),
    ('pinned', PINNED, None, 'the candidate is the baseline'),
    ('pinned', PINNED, confirm.PILOT_MAX_CASES + 1, '--limit-cases must be from 1 to 2'),
])
def test_a_pilot_is_only_a_build_against_itself_on_a_few_cases(held_out, baseline, candidate, limit, message):
    with pytest.raises(ValueError, match=message):
        confirm.confirm(baseline, candidate, limit=limit)
    assert not (held_out / 'confirmations/uses.json').exists()


def test_a_comparison_the_rule_cannot_make_is_counted_as_not_confirmed(held_out, monkeypatch):
    # The candidate sends no findings at all: its grounded F1 is undefined in every round.
    harbor(monkeypatch, dict(reviewbench=[counts(0, 0, 0, 0, 4, 0)] * 3, aacr=BASELINE['aacr']))
    assert confirm.confirm('pinned', CANDIDATE) == 'not-confirmed'
    result = json.loads((held_out / f'confirmations/{CANDIDATE}/result.json').read_text())
    assert 'undefined' in result['benchmarks']['reviewbench']['unusable']
    assert confirm.uses(held_out / 'confirmations') == {'reviewbench-test': 2, 'aacr-holdout': 1}


def test_a_held_out_case_that_fails_to_convert_once_is_not_left_out(held_out, monkeypatch):
    real, failures = aacr_convert.write_task, ['owner-project-3']
    def flaky(record, output, daydream, build_file, *slots):
        if aacr_convert.task_name(record) in failures:
            failures.remove(aacr_convert.task_name(record))
            raise OSError('disk full')
        return real(record, output, daydream, build_file, *slots)
    monkeypatch.setattr(aacr_convert, 'write_task', flaky)
    harbor(monkeypatch, dict(reviewbench=GAIN, aacr=BASELINE['aacr']))
    with pytest.raises(ValueError, match="These cases failed to convert: .*owner-project-3"):
        confirm.confirm('pinned', CANDIDATE)
    # Nothing was counted and no partial catalog stays: the next run converts every case.
    assert not (held_out / 'confirmations/uses.json').exists()
    assert not (held_out / f'confirmations/baselines/{PINNED}/aacr/catalog').exists()
    assert confirm.confirm('pinned', CANDIDATE) == 'confirmed'


def test_the_loop_cannot_be_pointed_at_confirmations_or_a_held_out_catalog(held_out, monkeypatch, capsys):
    harbor(monkeypatch, dict(reviewbench=GAIN, aacr=BASELINE['aacr']))
    assert confirm.confirm('pinned', CANDIDATE) == 'confirmed'
    summaries = [held_out / f'confirmations/baselines/{PINNED}/reviewbench/summary.json',
                 held_out / f'confirmations/{CANDIDATE}/reviewbench/summary.json']

    # loop.rounds: no split to ask for, a held-out case is refused, and a name can't leave artifacts/loop/.
    with pytest.raises(TypeError):
        rounds.run('pinned', held_out / 'cases.txt', 1, 'held-out', split='test')
    (held_out / 'cases.txt').write_text('case-a\ncase-t\n')
    with pytest.raises(ValueError, match=r"Unknown ReviewBench task names in the dev set: \['case-t'\]"):
        rounds.run('pinned', held_out / 'cases.txt', 1, 'held-out')
    for name in ('../../confirmations', f'../../confirmations/{CANDIDATE}/reviewbench', '/tmp/x'):
        with pytest.raises(ValueError, match='plain folder name'):
            rounds.run('pinned', held_out / 'cases.txt', 1, name)
    # loop.decide: a held-out summary is refused, even with every setting it needs.
    limits = settings.load(sigma=.02, rounds=3, precision_margin=.03, wrong_findings_margin=.2, incomplete_margin=.5)
    monkeypatch.setattr(settings, 'load', lambda: limits)
    capsys.readouterr()
    assert decide.main(['--baseline', str(summaries[0]), '--candidate', str(summaries[1]), '--stage', 'climb']) == 2
    assert 'held-out rounds belong to loop.confirm' in json.loads(capsys.readouterr().out)['error']
    # The ledger: a run is a folder under loop-runs/, so confirmations/ can't be named; a confirmation is one word.
    monkeypatch.setattr(ledger, 'RUNS', held_out / 'loop-runs')
    for command in ('verify', 'show', 'ideas'):
        with pytest.raises(SystemExit):
            ledger.main([command, '../confirmations'])
    (held_out / 'confirmations/ledger.jsonl').write_text('')
    assert ledger.main(['verify', 'confirmations']) == 1
    assert str(held_out / 'loop-runs/confirmations') in capsys.readouterr().err


def test_sides_whose_catalogs_differ_by_a_dropped_case_are_compared_on_the_cases_both_hold():
    limits = settings.load(sigma=.02, rounds=3, precision_margin=.03, wrong_findings_margin=.2, incomplete_margin=.5)
    def side(cases):
        rows = {case: [rb(2), rb(2), rb(1)] for case in cases}
        return dict(catalog_identity=dict(reviewbench_revision='r', split='test', judge='j', daydream={}),
                    rounds=[dict(round=n + 1) for n in range(3)], cases=rows)
    # The candidate's catalog lost case-c, as a commit gone upstream would leave it.
    result = confirm.compare(side(['case-a', 'case-b', 'case-c']), side(['case-a', 'case-b']), 'reviewbench', limits)
    assert result['matched'] == dict(cases=2, of=3) and 'unusable' in result
