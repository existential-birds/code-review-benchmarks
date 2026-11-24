"""loop.prune on a made-up run: test_loop_iterate.py's iterations, with a stand-in for Docker."""
import json
import subprocess

import pytest

from loop import ledger, prune, settings
from test_loop_iterate import LIMITS, loop, worktree  # noqa: F401 (loop is a fixture)
from test_loop_rounds import workspace  # noqa: F401 (a fixture loop uses)


def image(case, job):
    return f'{case}__{job}__env-main'


@pytest.fixture
def docker(monkeypatch):
    """Docker with a few images: one an accepted iteration's, one the baseline's and one never the loop's."""
    calls, images = [], {image('case-a', 'run-a-1-climb-round-1-attempt-1'): 1000,
                         image('case-b', 'run-a-1-climb-round-1-attempt-1'): 2000,
                         image('case-b', 'run-a-4-climb-round-1-attempt-1'): 3000,
                         image('case-a', 'run-a-2-climb-round-1-attempt-1'): 4000,
                         image('case-a', 'base-round-1-attempt-1'): 5000, 'node': 6000}

    def run(*arguments):
        calls.append(list(arguments))
        if arguments[:2] == ('image', 'ls'):
            out = ''.join(f'{name}\n' for name in images) + '<none>\n'
        elif arguments[:2] == ('image', 'inspect'):
            out = ''.join(f'{images[name]}\n' for name in arguments[4:])
        else:
            assert arguments[:2] == ('image', 'rm')
            del images[arguments[2]]
            out = ''
        return subprocess.CompletedProcess(['docker', *arguments], 0, out, '')
    monkeypatch.setattr(prune, 'docker', run)
    return calls


def files(root):
    return {path.relative_to(root).as_posix() for path in root.rglob('*')
            if path.is_file() and '.git' not in path.relative_to(root).parts}


def under(names, folder):
    return {name for name in names if name.startswith(folder + '/')}


def test_a_rejected_or_invalid_iterations_files_go_and_everything_else_stays(loop, git, docker):  # noqa: F811
    first = loop.run(['base'])
    accepted = loop.run(['win'] * 4)
    invalid = loop.run(build_fails=True)
    fourth = loop.run(['base'])
    assert [entry['verdict']['verdict'] for entry in (first, accepted, invalid, fourth)] == [
        'rejected', 'accepted-on-dev', 'invalid', 'rejected']
    # A worktree loop.iterate couldn't remove, and what the run must keep whatever happens.
    git(loop.clone, 'worktree', 'add', '-q', str(worktree(loop, 1)), 'loop/run-a/1')
    for path in ('confirmations/run-a-2/verdict.json', 'artifacts/loop/confirmations/2.json'):
        (loop.root / path).parent.mkdir(parents=True)
        (loop.root / path).write_text('{}')
    before = files(loop.root)
    one, four = (f'.cache/daydream-candidates/{entry["candidate"]["commit"]}' for entry in (first, fourth))
    parts = [under(before, folder) for folder in (
        'artifacts/loop/run-a-1-climb/catalog', 'artifacts/loop/run-a-4-climb/catalog', one, four,
        '.cache/loop/worktrees/run-a-1', 'jobs/run-a-1-climb-round-1-attempt-1')]
    gone = set().union(*parts)
    assert all(parts) and under(before, 'jobs/run-a-4-climb-round-1-attempt-1')
    images = [image('case-a', 'run-a-1-climb-round-1-attempt-1'), image('case-b', 'run-a-1-climb-round-1-attempt-1'),
              image('case-b', 'run-a-4-climb-round-1-attempt-1')]
    look = [['image', 'ls', '--format', '{{.Repository}}'], ['image', 'inspect', '--format', '{{.Size}}', *images]]

    # Iteration 1 has three later iterations, so with keep_rejected_jobs 3 its jobs go too.
    loaded = settings.load(**LIMITS)
    listed = prune.prune('run-a', loaded, dry_run=True, root=loop.root)
    assert files(loop.root) == before and docker == look
    assert listed['bytes_freed'] == 0 and listed['bytes'] == sum(item['bytes'] for item in listed['items']) > 6000
    assert [(item['iteration'], item['kind']) for item in listed['items']] == [
        (1, 'image'), (1, 'image'), (4, 'image'), (1, 'catalog'), (1, 'candidate build'), (1, 'worktree'),
        (1, 'job'), (4, 'catalog'), (4, 'candidate build')]
    assert [item['bytes'] for item in listed['items'][:3]] == [1000, 2000, 3000]
    assert listed['items'][3]['bytes'] == sum((loop.root / name).stat().st_size for name in
                                              under(before, 'artifacts/loop/run-a-1-climb/catalog'))

    docker.clear()
    removed = prune.prune('run-a', loaded, root=loop.root)
    assert removed['items'] == listed['items'] and removed['bytes_freed'] == listed['bytes']
    assert docker == look + [['image', 'rm', name] for name in images]
    assert files(loop.root) == before - gone
    # The branch stays in the clone, and the summaries stay beside the catalogs that went.
    assert git(loop.clone, 'rev-parse', 'loop/run-a/1') == first['candidate']['commit']
    assert (loop.root / 'artifacts/loop/run-a-1-climb/summary.json').is_file()
    assert {'loop-runs/run-a/ledger.jsonl', 'loop-runs/run-a/patches/1.patch', 'loop-runs/run-a/patches/4.patch',
            'artifacts/loop/base/catalog/catalog.json', 'artifacts/loop/base-check/catalog/catalog.json',
            'artifacts/loop/run-a-2-check/catalog/catalog.json', 'confirmations/run-a-2/verdict.json',
            f'.cache/daydream-candidates/{accepted["candidate"]["commit"]}/build.json'} <= files(loop.root)
    assert (loop.root / '.cache/loop/evidence/base').is_dir() and worktree(loop, 2).is_dir()

    # With none to keep, iteration 4's jobs go; its images went already.
    docker.clear()
    jobs = prune.prune('run-a', settings.load(**LIMITS, keep_rejected_jobs=0), root=loop.root)
    assert [(item['iteration'], item['kind'], item['path']) for item in jobs['items']] == [
        (4, 'job', 'jobs/run-a-4-climb-round-1-attempt-1')]
    assert docker == look[:1] and jobs['bytes_freed'] == jobs['bytes'] > 0
    assert files(loop.root) == before - gone - under(before, 'jobs/run-a-4-climb-round-1-attempt-1')
    again = prune.prune('run-a', settings.load(**LIMITS, keep_rejected_jobs=0), root=loop.root)
    assert again['items'] == [] and again['bytes_freed'] == 0


def test_an_image_docker_wont_remove_is_reported_and_the_rest_still_goes(loop, docker, monkeypatch, capsys):  # noqa: F811
    loop.run(['base'])
    stand_in = prune.docker

    def refuse(*arguments):
        if arguments[:2] == ('image', 'rm') and 'case-a' in arguments[2]:
            return subprocess.CompletedProcess(['docker', *arguments], 1, '', 'image is in use by container c1')
        return stand_in(*arguments)
    monkeypatch.setattr(prune, 'docker', refuse)
    monkeypatch.setattr(prune, 'ROOT', loop.root)
    assert prune.main(['--run', 'run-a']) == 1
    result = json.loads(capsys.readouterr().out)
    failed = [item for item in result['items'] if 'error' in item]
    assert [item['image'] for item in failed] == [image('case-a', 'run-a-1-climb-round-1-attempt-1')]
    assert failed[0]['error'] == 'docker image rm failed: image is in use by container c1'
    assert result['bytes_freed'] == result['bytes'] - 1000
    assert not (loop.root / 'artifacts/loop/run-a-1-climb/catalog').exists()


@pytest.mark.parametrize('marker', ['.cache/loop/logs/run-a-2', '.cache/loop/worktrees/run-a-2',
                                    'artifacts/loop/run-a-2-climb'])
def test_it_refuses_while_an_iteration_is_in_progress_unless_the_loop_calls_between_iterations(
        loop, docker, marker, monkeypatch, capsys):  # noqa: F811
    loop.run(['base'])
    (loop.root / marker).mkdir(parents=True)
    # Another run's iteration and the run's own finished ones don't count.
    (loop.root / '.cache/loop/logs/run-a-1').mkdir(parents=True, exist_ok=True)
    (loop.root / '.cache/loop/logs/run-b-7').mkdir()
    monkeypatch.setattr(prune, 'ROOT', loop.root)
    assert prune.main(['--run', 'run-a', '--dry-run']) == 2
    assert json.loads(capsys.readouterr().out)['error'].startswith('Iteration 2 of run-a has started')
    assert not docker and (loop.root / 'artifacts/loop/run-a-1-climb/catalog').is_dir()
    result = prune.prune('run-a', settings.load(**LIMITS), between_iterations=True, root=loop.root)
    assert result['bytes_freed'] > 0 and not (loop.root / 'artifacts/loop/run-a-1-climb/catalog').exists()
    assert [entry['kind'] for entry in ledger.read(loop.root / 'loop-runs/run-a')] == ['run-start', 'iteration']
