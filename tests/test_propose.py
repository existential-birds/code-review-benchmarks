"""The proposer step against a local bare repository standing in for Daydream on GitHub.

Worktrees and patch IDs are Git's, so these tests use the real Git through the
conftest's ``git`` fixture. The proposer is a stand-in that follows a plan: per
attempt, the commits to make, the proposal.json to write and its exit code. It
records the prompt, environment and directory it was given.
"""
import json
from pathlib import Path
import sys

import pytest

from loop import guard, ledger, propose, settings
from real_git import commit

DAYDREAM = {'AGENTS.md': '# Agent guidance\n', 'daydream/config.py': 'EFFORT = "medium"\n',
            'daydream/prompts/review.py': 'PROMPT = "Review the change."\n'}
EFFORT = {'daydream/config.py': 'EFFORT = "high"\n'}
PROMPT = {'daydream/prompts/review.py': 'PROMPT = "Review the change and its callers."\n'}
STAND_IN = r'''import json, os, pathlib, re, shlex, subprocess, sys, time
plan_path, git = pathlib.Path(sys.argv[1]), sys.argv[2]
record = plan_path.with_name('calls.json')
calls = json.loads(record.read_text()) if record.exists() else []
prompt = sys.stdin.read()
step = json.loads(plan_path.read_text())[len(calls)]
calls.append(dict(prompt=prompt, environment=dict(os.environ), directory=os.getcwd(),
                  head=subprocess.run([git, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()))
record.write_text(json.dumps(calls))
environment = dict(os.environ, GIT_CONFIG_NOSYSTEM='1')
for number, files in enumerate(step.get('commits', [])):
    for name, content in files.items():
        pathlib.Path(name).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(name).write_text(content)
    subprocess.run([git, 'add', '-A'], check=True, env=environment)
    subprocess.run([git, 'commit', '-q', '-m', f'feat: change {number}'], check=True, env=environment)
if 'proposal' in step:
    target = pathlib.Path(shlex.split(re.search(r'^    PROPOSAL=(.+)$', prompt, re.M)[1])[0])
    target.write_text(step['proposal'] if isinstance(step['proposal'], str) else json.dumps(step['proposal']))
time.sleep(step.get('sleep', 0))
sys.exit(step.get('exit', 0))
'''


def proposal(files=('daydream/config.py',), **fields):
    return {'hypothesis': 'A higher arbiter effort keeps more true findings.', 'category': 'finding_quality',
            'change': {'pattern': 'arbiter-effort', 'description': 'Raise the arbiter phase effort to high.'},
            'prediction': 'Grounded F1 rises by about 0.02.', 'quality_guardrails': ['Findings ruled wrong stay level.'],
            'rejection_criterion': 'The gain stays below the threshold.',
            'evidence': ['index.json', 'cases/case-a.json#/missed/0'], 'files': list(files), **fields}


@pytest.fixture
def remote(tmp_path, git):
    """A bare repository as Daydream's remote, its source repository and base commit."""
    source = tmp_path / 'source'
    source.mkdir()
    git(source, 'init', '-q', '-b', 'main')
    base = commit(git, source, DAYDREAM)
    git(tmp_path, 'clone', '-q', '--bare', str(source), str(tmp_path / 'daydream.git'))
    return tmp_path / 'daydream.git', source, base


@pytest.fixture
def pack(tmp_path):
    """A read-only evidence pack."""
    pack = tmp_path / 'pack'
    (pack / 'cases').mkdir(parents=True)
    (pack / 'index.json').write_text('{"totals": {}}\n')
    (pack / 'labels.jsonl').write_text('')
    (pack / 'cases/case-a.json').write_text('{"missed": [{"message": "Unchecked error"}]}\n')
    for path in (pack / 'cases/case-a.json', pack / 'index.json', pack / 'labels.jsonl', pack / 'cases', pack):
        path.chmod(0o555 if path.is_dir() else 0o444)
    yield pack
    for path in (pack, pack / 'cases'):
        path.chmod(0o755)


def stand_in(folder, plan):
    """A proposer command following the plan, and a function returning what it was given."""
    folder.mkdir()
    (folder / 'plan.json').write_text(json.dumps(plan))
    (folder / 'stand_in.py').write_text(STAND_IN)
    command = [sys.executable, str(folder / 'stand_in.py'), str(folder / 'plan.json'), guard.GIT]
    return command, lambda: json.loads((folder / 'calls.json').read_text())


@pytest.fixture
def step(tmp_path, remote, pack, monkeypatch):
    """Run the step with a stand-in proposer following a plan; returns its result and calls."""
    monkeypatch.setattr(propose, 'CLONE_URL', str(remote[0]))
    monkeypatch.setattr(propose, 'ROOT', tmp_path / 'suite')

    def run(plan, baseline=remote[2], timeout=30, **overrides):
        command, calls = stand_in(tmp_path / f'proposer-{len(list(tmp_path.glob("proposer-*")))}', plan)
        result = propose.propose('run-a', baseline, pack, settings.load(proposer_command=command, **overrides),
                                 root=tmp_path / 'suite', url=str(remote[0]), timeout=timeout)
        return result, calls()
    return run


def start_run(root, *iterations, name='run-a'):
    run = root / 'loop-runs' / name
    ledger.append(run, 'run-start', dict(settings={}, suite_commit='a' * 40, judge='pi', baseline_commit='b' * 40,
                                         baseline_summary_sha256='0' * 64, canary={}))
    for entry in iterations:
        ledger.append(run, 'iteration', entry, patch='' if 'candidate' in entry else None)
    return run


def iteration(n, verdict, patch_id, **fields):
    return dict(iteration=n, verdict=dict(verdict=verdict, reasons=['gain below threshold']),
                proposal=proposal(**fields), candidate=dict(commit='c' * 40, patch_id=patch_id))


def same_patch(git, source, base, files):
    """The patch ID of the change ``files`` make to the base, made on a side branch of the source."""
    git(source, 'checkout', '-q', '-b', f'side-{len(git(source, "branch").split())}', base)
    return propose.patch_id(source, base, commit(git, source, files))


def test_the_step_clones_fetches_cuts_a_worktree_and_reports_one_proposal(tmp_path, remote, step, git, monkeypatch,
                                                                         capsys, pack):
    bare, source, base = remote
    monkeypatch.setenv('GITHUB_TOKEN', 'not for the proposer')
    result, calls = step([dict(commits=[EFFORT], proposal=proposal())])
    root = tmp_path / 'suite'
    worktree = root / '.cache/loop/worktrees/run-a-1'
    assert result | dict(commit=None, patch_id=None) == dict(
        run='run-a', iteration=1, baseline=base, branch='loop/run-a/1', worktree=str(worktree),
        logs=str(root / '.cache/loop/logs/run-a-1'), proposal=proposal(), commit=None, patch_id=None,
        diff=dict(files=1, lines=2), attempts=1, refusals=[])
    assert git(worktree, 'rev-parse', 'HEAD') == result['commit'] == git(worktree, 'rev-parse', 'loop/run-a/1')
    assert git(worktree, 'rev-parse', 'HEAD^') == base
    assert result['patch_id'] == same_patch(git, source, base, EFFORT)
    assert git(root / '.cache/loop/daydream', 'remote', 'get-url', 'origin') == str(bare)

    call = calls[0]
    assert Path(call['directory']) == worktree and call['head'] == base
    home = Path(call['environment']['HOME'])
    assert call['environment'].keys() <= {*propose.ENVIRONMENT, 'HOME', 'CODEX_HOME', 'LC_CTYPE'}
    assert call['environment']['CODEX_HOME'] == str(tmp_path / 'home/.codex')
    assert not home.exists() and not home.is_relative_to(root)
    told = call['prompt']
    assert told.startswith(propose.PROMPT.read_text()) and propose.SEEDS.read_text() in told
    assert 'At most 5 files and 500 lines changed' in told and '`uv.lock`' in told and 'None yet.' in told
    assert (root / '.cache/loop/logs/run-a-1/attempt-1.prompt.md').read_text() == told
    # The proposer got a copy of the pack outside the suite, gone once the step ends.
    copy = Path(told.split('PACK=', 1)[1].split('\n', 1)[0])
    assert copy.parent == home.parent and not copy.is_relative_to(root) and not copy.exists()

    # A second iteration, through the command line, fetches a baseline pushed since and
    # is told the first one's idea.
    start_run(root, iteration(1, 'rejected', result['patch_id']))
    newer = commit(git, source, PROMPT)
    git(source, 'push', '-q', str(bare), 'HEAD:main')
    change = {'daydream/prompts/review.py': 'PROMPT = "Review every caller."\n'}
    command, calls = stand_in(tmp_path / 'second', [dict(commits=[change],
                                                         proposal=proposal(files=['daydream/prompts/review.py']))])
    load = settings.load
    monkeypatch.setattr(settings, 'load', lambda: load(proposer_command=command))
    assert propose.main(['--run', 'run-a', '--baseline', newer, '--evidence', str(pack)]) == 0
    second = json.loads(capsys.readouterr().out)
    assert (second['iteration'], second['baseline'], second['refusals']) == (2, newer, [])
    assert git(root / '.cache/loop/worktrees/run-a-2', 'rev-parse', 'HEAD^') == newer
    assert '- run-a #1 rejected [finding_quality] A higher arbiter effort' in calls()[0]['prompt']


def test_a_repeated_patch_is_refused_and_the_proposer_retries_from_the_baseline_with_the_reason(
        tmp_path, remote, step, git):
    _, source, base = remote
    # Any earlier run's patch counts, whatever its verdict.
    start_run(tmp_path / 'suite', iteration(4, 'invalid', same_patch(git, source, base, EFFORT)), name='run-0')
    result, calls = step([dict(commits=[EFFORT], proposal=proposal()),
                          dict(commits=[PROMPT], proposal=proposal(files=['daydream/prompts/review.py']))])
    assert result['refusals'] == [dict(attempt=1, reason='The commit repeats the patch of run-0 #4')]
    assert result['attempts'] == 2 and result['proposal']['files'] == ['daydream/prompts/review.py']
    assert calls[1]['head'] == base and git(result['worktree'], 'status', '--porcelain') == ''
    assert '## Refused attempts' not in calls[0]['prompt']
    assert '- Attempt 1: The commit repeats the patch of run-0 #4' in calls[1]['prompt']


def test_the_category_and_files_of_a_rejected_iteration_are_refused_unless_named_with_how_it_differs(
        tmp_path, step):
    start_run(tmp_path / 'suite', iteration(1, 'rejected', '1' * 40),
              # An idea accepted on dev, or invalid, may be built on.
              iteration(2, 'accepted-on-dev', '2' * 40), iteration(3, 'invalid', '3' * 40))
    differs = [dict(run='run-a', iteration=1, how='A smaller step, from medium to high rather than xhigh.')]
    result, _ = step([dict(commits=[EFFORT], proposal=proposal()),
                      dict(commits=[EFFORT], proposal=proposal(differs_from=differs))])
    assert result['refusals'] == [dict(attempt=1, reason=(
        "Category finding_quality and files ['daydream/config.py'] are those of rejected run-a #1; change "
        'something else, or name it in differs_from and say how this differs'))]
    assert result['proposal']['differs_from'] == differs and result['iteration'] == 4


def test_after_max_proposal_attempts_refusals_it_reports_no_proposal(tmp_path, remote, step, git):
    result, calls = step([dict(proposal=proposal()),
                          dict(commits=[EFFORT, PROMPT], proposal=proposal()),
                          dict(commits=[EFFORT], proposal=proposal(), exit=3),
                          dict(commits=[EFFORT], proposal=proposal(), sleep=30)], timeout=1, max_proposal_attempts=4)
    assert [refusal['reason'] for refusal in result['refusals']] == [
        'The proposer made no commit; make exactly one on top of the baseline',
        'The proposer made 2 commits; make exactly one on top of the baseline',
        'The proposer exited 3', 'The proposer ran past 1 seconds']
    assert result | dict(refusals=None) == dict(
        run='run-a', iteration=1, baseline=remote[2], branch='loop/run-a/1', worktree=result['worktree'],
        logs=result['logs'], proposal=None, commit=None, patch_id=None, diff=None, attempts=4, refusals=None,
        reason='No proposal: all 4 attempts were refused')
    assert len(calls) == 4 and git(result['worktree'], 'rev-parse', 'HEAD') == remote[2]


@pytest.mark.parametrize('written, reason', [
    (None, 'No proposal.json at '),
    ('{"hypothesis": NaN}', 'proposal.json is not strict JSON'),
    (proposal(category='prompts'), 'category must be one of'),
    (proposal(files=['daydream/prompts/review.py']), "files must list exactly the commit's files"),
    (proposal(evidence=['../jobs/result.json']), "evidence '../jobs/result.json' names no file in the pack"),
    (proposal(change={'pattern': 'Arbiter Effort', 'description': 'Raise it.'}), 'change.pattern must be a lowercase'),
    (proposal(quality_guardrails=[]), 'quality_guardrails must be a nonempty list'),
    (proposal(heldout_workloads=['a']), "unknown fields ['heldout_workloads']"),
    ({key: value for key, value in proposal().items() if key != 'prediction'}, "lacks ['prediction']"),
])
def test_an_invalid_proposal_is_refused(step, written, reason):
    result, _ = step([dict(commits=[EFFORT], **({} if written is None else dict(proposal=written)))],
                     max_proposal_attempts=1)
    assert result['proposal'] is None and reason in result['refusals'][0]['reason']
