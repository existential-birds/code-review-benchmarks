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

from loop import ledger, propose, settings
from real_git import commit

DAYDREAM = {'AGENTS.md': '# Agent guidance\n', 'daydream/config.py': 'EFFORT = "medium"\n',
            'daydream/prompts/review.py': 'PROMPT = "Review the change."\n'}
EFFORT = {'daydream/config.py': 'EFFORT = "high"\n'}
PROMPT = {'daydream/prompts/review.py': 'PROMPT = "Review the change and its callers."\n'}
STAND_IN = r'''import json, os, pathlib, sys, time
plan_path, scratch = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
record = plan_path.with_name('calls.json')
calls = json.loads(record.read_text()) if record.exists() else []
prompt = sys.stdin.read()
step = json.loads(plan_path.read_text())[len(calls)]
work = scratch / 'work'
calls.append(dict(prompt=prompt, directory=os.getcwd(), files=sorted(
    str(path.relative_to(work)) for path in work.rglob('*') if path.is_file()),
    config=(scratch / 'codex/config.toml').read_text(), pack=sorted(p.name for p in (scratch / 'evidence').iterdir())))
record.write_text(json.dumps(calls))
for files in step.get('edits', []):
    for name, content in files.items():
        if content is None:
            (work / name).unlink()
        else:
            if (work / name).is_dir():
                (work / name).rmdir()
            (work / name).parent.mkdir(parents=True, exist_ok=True)
            (work / name).write_text(content)
for name, target in step.get('links', {}).items():
    (work / name).symlink_to(target)
for name in step.get('git', []):
    (work / name).mkdir(parents=True)
if 'proposal' in step:
    target = scratch / 'out/proposal.json'
    target.write_text(step['proposal'] if isinstance(step['proposal'], str) else json.dumps(step['proposal']))
time.sleep(step.get('sleep', 0))
sys.exit(step.get('exit', 0))
'''


def proposal(files=('daydream/config.py',), **fields):
    return {'hypothesis': 'A higher arbiter effort keeps more true findings.', 'category': 'finding_quality',
            'change': {'pattern': 'arbiter-effort', 'description': 'Raise the arbiter phase effort to high.'},
            'prediction': 'Grounded F1 rises by about 0.02.', 'quality_guardrails': ['Findings ruled wrong stay level.'],
            'rejection_criterion': 'The gain stays below the threshold.',
            'evidence': ['index.json', 'cases/case-a.json#/missed/0'], 'files': list(files),
            'message': 'feat(arbiter): raise the arbiter effort to high', **fields}


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
    """A launcher running a stand-in proposer on the host that follows the plan, and its calls."""
    folder.mkdir()
    (folder / 'plan.json').write_text(json.dumps(plan))
    (folder / 'stand_in.py').write_text(STAND_IN)
    def launch(argv, scratch, name):
        assert argv == settings.load()['proposer_command'] and name.startswith('loop-propose-')
        return [sys.executable, str(folder / 'stand_in.py'), str(folder / 'plan.json'), str(scratch)]
    return launch, lambda: json.loads((folder / 'calls.json').read_text())


@pytest.fixture
def step(tmp_path, remote, pack, monkeypatch):
    """Run the step with a stand-in proposer following a plan; returns its result and calls."""
    monkeypatch.setattr(propose, 'CLONE_URL', str(remote[0]))
    monkeypatch.setattr(propose, 'ROOT', tmp_path / 'suite')
    monkeypatch.setattr(propose, 'stop', lambda name: None)

    def run(plan, baseline=remote[2], timeout=30, **overrides):
        launch, calls = stand_in(tmp_path / f'proposer-{len(list(tmp_path.glob("proposer-*")))}', plan)
        monkeypatch.setattr(propose, 'launch', launch)
        result = propose.propose('run-a', baseline, pack, settings.load(**overrides),
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


def test_the_step_clones_fetches_cuts_a_worktree_and_commits_the_proposers_one_change(
        tmp_path, remote, step, git, monkeypatch, capsys, pack):
    bare, source, base = remote
    result, calls = step([dict(edits=[EFFORT], proposal=proposal())])
    root = tmp_path / 'suite'
    worktree = root / '.cache/loop/worktrees/run-a-1'
    assert result | dict(commit=None, patch_id=None) == dict(
        run='run-a', iteration=1, baseline=base, branch='loop/run-a/1', worktree=str(worktree),
        logs=str(root / '.cache/loop/logs/run-a-1'), proposal=proposal(), commit=None, patch_id=None,
        diff=dict(files=1, lines=2), attempts=1, refusals=[])
    assert git(worktree, 'rev-parse', 'HEAD') == result['commit'] == git(worktree, 'rev-parse', 'loop/run-a/1')
    assert git(worktree, 'rev-parse', 'HEAD^') == base
    assert git(worktree, 'log', '-1', '--format=%s%n%an') == ('feat(arbiter): raise the arbiter effort to high\n'
                                                               'Daydream loop proposer')
    assert result['patch_id'] == same_patch(git, source, base, EFFORT)
    assert git(root / '.cache/loop/daydream', 'remote', 'get-url', 'origin') == str(bare)

    # The proposer saw a copy of the baseline's files, the pack and a Codex config, outside the suite.
    call = calls[0]
    scratch = Path(call['directory'])
    assert not scratch.is_relative_to(root) and not scratch.exists()
    assert call['files'] == sorted(DAYDREAM) and call['config'] == propose.CODEX_CONFIG
    assert call['pack'] == ['cases', 'index.json', 'labels.jsonl']
    told = call['prompt']
    assert told.startswith(propose.PROMPT.read_text()) and propose.SEEDS.read_text() in told
    assert '    WORK=/work\n' in told and '    PROPOSAL=/out/proposal.json\n' in told
    assert 'At most 5 files and 500 lines changed' in told and '`uv.lock`' in told and 'None yet.' in told
    assert (root / '.cache/loop/logs/run-a-1/attempt-1.prompt.md').read_text() == told

    # A second iteration, through the command line, takes a baseline pushed since by its branch name
    # and is told the first one's idea.
    start_run(root, iteration(1, 'rejected', result['patch_id']))
    newer = commit(git, source, PROMPT)
    git(source, 'push', '-q', str(bare), 'HEAD:main')
    change = {'daydream/prompts/review.py': 'PROMPT = "Review every caller."\n'}
    launch, calls = stand_in(tmp_path / 'second', [dict(edits=[change],
                                                       proposal=proposal(files=['daydream/prompts/review.py']))])
    monkeypatch.setattr(propose, 'launch', launch)
    assert propose.main(['--run', 'run-a', '--baseline', 'main', '--evidence', str(pack)]) == 0
    second = json.loads(capsys.readouterr().out)
    assert (second['iteration'], second['baseline'], second['refusals']) == (2, newer, [])
    assert git(root / '.cache/loop/worktrees/run-a-2', 'rev-parse', 'HEAD^') == newer
    assert '- run-a #1 rejected [finding_quality] A higher arbiter effort' in calls()[0]['prompt']


def test_a_repeated_patch_is_refused_and_the_proposer_retries_from_the_baseline_with_the_reason(
        tmp_path, remote, step, git):
    _, source, base = remote
    # Any earlier run's patch counts, unless its iteration was invalid: that said nothing about the idea.
    start_run(tmp_path / 'suite', iteration(4, 'rejected', same_patch(git, source, base, EFFORT)), name='run-0')
    start_run(tmp_path / 'suite', iteration(1, 'invalid', same_patch(git, source, base, PROMPT)), name='run-1')
    result, calls = step([dict(edits=[EFFORT], proposal=proposal()),
                          dict(edits=[PROMPT], proposal=proposal(files=['daydream/prompts/review.py']))])
    assert result['refusals'] == [dict(attempt=1, reason='The change repeats the patch of run-0 #4')]
    assert result['attempts'] == 2 and result['proposal']['files'] == ['daydream/prompts/review.py']
    assert calls[1]['files'] == sorted(DAYDREAM)
    assert git(result['worktree'], 'status', '--porcelain') == ''
    assert '## Refused attempts' not in calls[0]['prompt']
    assert '- Attempt 1: The change repeats the patch of run-0 #4' in calls[1]['prompt']


def test_the_category_and_files_of_a_rejected_iteration_are_refused_unless_named_with_how_it_differs(
        tmp_path, step):
    start_run(tmp_path / 'suite', iteration(1, 'rejected', '1' * 40),
              # An idea accepted on dev, or invalid, may be built on.
              iteration(2, 'accepted-on-dev', '2' * 40), iteration(3, 'invalid', '3' * 40))
    differs = [dict(run='run-a', iteration=1, how='A smaller step, from medium to high rather than xhigh.')]
    result, _ = step([dict(edits=[EFFORT], proposal=proposal()),
                      dict(edits=[EFFORT], proposal=proposal(differs_from=differs))])
    assert result['refusals'] == [dict(attempt=1, reason=(
        "Category finding_quality and files ['daydream/config.py'] are those of rejected run-a #1; change "
        'something else, or name it in differs_from and say how this differs'))]
    assert result['proposal']['differs_from'] == differs and result['iteration'] == 4


def test_after_max_proposal_attempts_refusals_it_reports_no_proposal(tmp_path, remote, step, git):
    result, calls = step([dict(proposal=proposal()),
                          dict(edits=[EFFORT], proposal=proposal(), exit=3),
                          dict(edits=[EFFORT], proposal=proposal(), sleep=30)], timeout=1, max_proposal_attempts=3)
    assert [refusal['reason'] for refusal in result['refusals']] == [
        'The proposer changed nothing; change one thing in $WORK', 'The proposer exited 3',
        'The proposer ran past 1 seconds']
    assert result | dict(refusals=None) == dict(
        run='run-a', iteration=1, baseline=remote[2], branch='loop/run-a/1', worktree=result['worktree'],
        logs=result['logs'], proposal=None, commit=None, patch_id=None, diff=None, attempts=3, refusals=None,
        reason='No proposal: all 3 attempts were refused')
    assert len(calls) == 3 and git(result['worktree'], 'rev-parse', 'HEAD') == remote[2]


@pytest.mark.parametrize('extra, reason', [
    (dict(links={'daydream/escape.py': '/etc/passwd'}), 'adds or alters a link: daydream/escape.py'),
    (dict(git=['.git/hooks']), 'adds a .git entry: .git'),
    (dict(git=['daydream/.git']), 'adds a .git entry: daydream/.git'),
    # On a disk that ignores case, as macOS's does by default, .GIT would be the worktree's .git file.
    (dict(edits=[{'.GIT': 'gitdir: elsewhere\n'}]), 'adds a .git entry: .GIT'),
])
def test_a_link_or_git_folder_left_in_the_copy_is_refused_and_never_reaches_the_worktree(step, extra, reason):
    result, _ = step([dict(dict(edits=[EFFORT], proposal=proposal()), **extra)], max_proposal_attempts=1)
    assert result['proposal'] is None and reason in result['refusals'][0]['reason']
    worktree = Path(result['worktree'])
    assert not (worktree / 'daydream/escape.py').exists() and not (worktree / 'daydream/.git').exists()


def test_a_deleted_file_is_committed_as_a_deletion(step, git):
    result, _ = step([dict(edits=[{'daydream/prompts/review.py': None}],
                           proposal=proposal(files=['daydream/prompts/review.py']))])
    assert git(result['worktree'], 'show', '--name-status', '--format=', 'HEAD') == 'D\tdaydream/prompts/review.py'


@pytest.mark.parametrize('written, reason', [
    (None, 'No proposal.json at '),
    ('{"hypothesis": NaN}', 'proposal.json is not strict JSON'),
    (proposal(category='prompts'), 'category must be one of'),
    (proposal(files=['daydream/prompts/review.py']), 'files must list exactly the files the change touches'),
    (proposal(evidence=['../jobs/result.json']), "evidence '../jobs/result.json' names no file in the pack"),
    (proposal(change={'pattern': 'Arbiter Effort', 'description': 'Raise it.'}), 'change.pattern must be a lowercase'),
    (proposal(quality_guardrails=[]), 'quality_guardrails must be a nonempty list'),
    (proposal(message='Raise the arbiter effort'), 'message must start with a Conventional Commit line'),
    (proposal(heldout_workloads=['a']), "unknown fields ['heldout_workloads']"),
    ({key: value for key, value in proposal().items() if key != 'prediction'}, "lacks ['prediction']"),
])
def test_an_invalid_proposal_is_refused(step, written, reason):
    result, _ = step([dict(edits=[EFFORT], **({} if written is None else dict(proposal=written)))],
                     max_proposal_attempts=1)
    assert result['proposal'] is None and reason in result['refusals'][0]['reason']


def test_the_container_mounts_only_the_proposers_folders_and_the_codex_login(tmp_path, monkeypatch):
    login = tmp_path / 'codex-login'
    login.mkdir()
    (login / 'auth.json').write_text('{}')
    monkeypatch.setenv('REVIEWER_CODEX_AUTH_DIR', str(login))
    monkeypatch.setattr(propose, 'image', lambda: 'proposer-image')
    scratch = tmp_path / 'scratch'
    command = propose.container(['codex', 'exec'], scratch, 'loop-propose-run-a-1-1')
    volumes = [command[at + 1] for at, part in enumerate(command) if part == '--volume']
    assert volumes == [f'{scratch}/work:/work:rw', f'{scratch}/evidence:/pack:ro', f'{scratch}/analysis:/analysis:rw',
                       f'{scratch}/out:/out:rw', f'{scratch}/codex:/codex:rw', f'{login}/auth.json:/codex/auth.json:rw']
    assert command[-3:] == ['proposer-image', 'codex', 'exec'] and '--rm' in command
    assert 'CODEX_HOME=/codex' in command and not any('GITHUB' in part for part in command)
    # The image is the reviewer's pinned Node, Python and Codex CLI, with no Daydream in it.
    assert propose.build.NODE_IMAGE in propose.DOCKERFILE and propose.build.BUILD['codex_cli'] in propose.DOCKERFILE
    (login / 'auth.json').unlink()
    with pytest.raises(ValueError, match='No Codex login'):
        propose.container(['codex', 'exec'], scratch, 'x')


def test_a_folder_of_the_baseline_replaced_by_a_file_is_committed(step, git):
    result, _ = step([dict(edits=[{'daydream/prompts/review.py': None}, {'daydream/prompts': 'PROMPTS = {}\n'}],
                           proposal=proposal(files=['daydream/prompts', 'daydream/prompts/review.py']))])
    assert result['proposal'], result['refusals']
    assert git(result['worktree'], 'show', '--name-status', '--format=', 'HEAD').split('\n') == [
        'A\tdaydream/prompts', 'D\tdaydream/prompts/review.py']


def test_a_link_in_the_baseline_is_kept_and_a_folder_may_replace_it(tmp_path, git, pack, monkeypatch):
    source = tmp_path / 'linked'
    source.mkdir()
    git(source, 'init', '-q', '-b', 'main')
    (source / 'docs').mkdir()
    (source / 'docs/guide.md').write_text('Guide\n')
    (source / 'CLAUDE.md').symlink_to('AGENTS.md')
    base = commit(git, source, DAYDREAM)
    git(tmp_path, 'clone', '-q', '--bare', str(source), str(tmp_path / 'linked.git'))
    for number, edits, files in ((1, [EFFORT], ['daydream/config.py']),
                                 (2, [{'CLAUDE.md': None}, {'CLAUDE.md/notes.md': 'Notes\n'}],
                                  ['CLAUDE.md', 'CLAUDE.md/notes.md'])):
        launch, _ = stand_in(tmp_path / f'linked-{number}', [dict(edits=edits, proposal=proposal(files=files))])
        monkeypatch.setattr(propose, 'launch', launch)
        monkeypatch.setattr(propose, 'stop', lambda name: None)
        result = propose.propose(f'run-{number}', base, pack, settings.load(), root=tmp_path / 'suite',
                                 url=str(tmp_path / 'linked.git'), timeout=30)
        assert result['proposal'] and result['diff']['files'] == len(files)
    assert git(Path(result['worktree']), 'show', '--name-status', '--format=', 'HEAD').split('\n') == [
        'D\tCLAUDE.md', 'A\tCLAUDE.md/notes.md']


def test_a_failure_to_start_the_proposer_leaves_no_worktree_or_branch_behind(tmp_path, remote, pack, monkeypatch,
                                                                           capsys, git):
    monkeypatch.setattr(propose, 'ROOT', tmp_path / 'suite')
    monkeypatch.setattr(propose, 'CLONE_URL', str(remote[0]))
    def no_docker(argv, scratch, name):
        raise FileNotFoundError('docker')
    monkeypatch.setattr(propose, 'launch', no_docker)
    monkeypatch.setattr(propose, 'stop', lambda name: None)
    for _ in range(2):
        assert propose.main(['--run', 'run-a', '--baseline', remote[2], '--evidence', str(pack)]) == 2
        assert 'FileNotFoundError' in json.loads(capsys.readouterr().out)['error']
    assert not (tmp_path / 'suite/.cache/loop/worktrees/run-a-1').exists()
    assert git(tmp_path / 'suite/.cache/loop/daydream', 'branch', '--list', 'loop/*') == ''


def test_the_loops_git_takes_no_driver_from_the_operators_configuration(tmp_path, git, monkeypatch):
    marker = tmp_path / 'ran'
    (tmp_path / 'operator.gitconfig').write_text(f'[filter "p"]\n\tclean = touch {marker}; cat\n')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(tmp_path / 'operator.gitconfig'))
    repository = tmp_path / 'repository'
    repository.mkdir()
    propose.guard.git(repository, 'init', '-q')
    (repository / '.gitattributes').write_text('* filter=p\n')
    (repository / 'file.py').write_text('x = 1\n')
    propose.guard.git(repository, 'add', '--all')
    assert not marker.exists()
