"""Stand-ins for Git, so tests never run it and never create commits.

Git itself is trusted. These fakes answer only the calls the suite makes, so tests
cover the suite's own decisions about what to fetch, compare and reject.
"""
from pathlib import Path
import subprocess

BASE, HEAD = 'a' * 40, 'b' * 40
PATCH = ('diff --git a/file.php b/file.php\n--- a/file.php\n+++ b/file.php\n'
         '@@ -1 +1 @@\n-base\n+head\n')


def _key(path):
    return str(Path(path).resolve())


class FakeGitCli:
    """Replaces the ``subprocess`` module in ``benchmarks.aacr.convert``.

    ``checkouts`` maps a source checkout to its ``head`` and ``status``. A context
    repository's fetches walk through ``rounds``: each round gives what
    ``merge-base`` finds, the shallow boundary commits, and the commits
    ``rev-list`` reports outside the merge base.
    """
    CompletedProcess = subprocess.CompletedProcess
    CalledProcessError = subprocess.CalledProcessError

    def __init__(self):
        self.commands = []
        self.checkouts = {}
        self.unavailable = set()
        self.rounds = None
        self.context_status = ''
        self._contexts = {}

    def checkout(self, path, head, status=''):
        self.checkouts[_key(path)] = dict(head=head, status=status)

    def fetches(self):
        return [args for args in self.commands if args[0] == 'fetch']

    def run(self, command, **_):
        assert command[:2] == ['git', '-C'], command
        path, args = command[2], command[3:]
        self.commands.append(tuple(args))
        if _key(path) in self.checkouts:
            checkout = self.checkouts[_key(path)]
            return self._answer(command, {('rev-parse', 'HEAD'): checkout['head'],
                                          ('status', '--porcelain'): checkout['status']}[tuple(args)])
        return self._context(command, Path(path), args)

    def check_output(self, command, **kwargs):
        result = self.run(command, **kwargs)
        if result.returncode:
            raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
        return result.stdout

    def _answer(self, command, stdout='', returncode=0, stderr=''):
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    def _context(self, command, path, args):
        state = self._contexts.setdefault(_key(path), dict(round=-1, head=None))
        rounds = self.rounds or [dict(merge_base=None, shallow=[], exclusive=[])]
        current = rounds[max(state['round'], 0)]
        if args[0] == 'init':
            (path / '.git').mkdir(parents=True)
        elif args[0] == 'fetch':
            revisions = args[args.index('origin') + 1:]
            if self.unavailable.intersection(revisions):
                return self._answer(command, returncode=128, stderr='fatal: not our ref')
            state['round'] += 1
            shallow = rounds[state['round']]['shallow']
            (path / '.git/shallow').write_text(''.join(f'{commit}\n' for commit in shallow))
        elif args[0] == 'merge-base':
            common = current['merge_base'] if self.rounds else args[1]
            return self._answer(command, f'{common}\n' if common else '', 0 if common else 1)
        elif args[0] == 'rev-list':
            return self._answer(command, ''.join(f'{commit}\n' for commit in current['exclusive']))
        elif args[0] == 'checkout':
            state['head'] = args[-1]
        elif args == ['rev-parse', 'HEAD']:
            return self._answer(command, f'{state["head"]}\n')
        elif args == ['status', '--porcelain']:
            return self._answer(command, self.context_status)
        return self._answer(command)


class FakeCheckout:
    """Daydream's ``git_ops`` view of one checkout.

    The revisions and patch are fixed. ``status_porcelain`` reports real changes
    to the files on disk, so a reviewer that edits the checkout is still caught.
    """

    def __init__(self, repo, base=BASE, head=HEAD, patch=PATCH):
        self.repo, self.base, self.head, self.patch = Path(repo), base, head, patch
        self.snapshot = {path: path.read_bytes() for path in self.repo.rglob('*') if path.is_file()}
        self.diff_environments = []

    def install(self, monkeypatch):
        from daydream import git_ops
        monkeypatch.setattr(git_ops, 'head_sha', self.head_sha)
        monkeypatch.setattr(git_ops, 'diff', self.diff)
        monkeypatch.setattr(git_ops, 'merge_base', self.merge_base)
        monkeypatch.setattr(git_ops, 'resolve_diff_merge_base', self.merge_base)
        monkeypatch.setattr(git_ops, 'status_porcelain', self.status_porcelain)
        return self

    def _check(self, repo):
        assert Path(repo) == self.repo, repo

    def head_sha(self, repo):
        self._check(repo)
        return self.head

    def diff(self, repo, base, head='HEAD', **_):
        import os
        self._check(repo)
        self.diff_environments.append(dict(os.environ))
        return self.patch if (base, head) in ((self.base, 'HEAD'), (self.base, self.head)) else ''

    def merge_base(self, repo, base, head='HEAD'):
        self._check(repo)
        return self.base

    def status_porcelain(self, repo):
        self._check(repo)
        lines = []
        current = {path for path in self.repo.rglob('*') if path.is_file()}
        for path in sorted(current | set(self.snapshot)):
            name = path.relative_to(self.repo).as_posix()
            if path not in self.snapshot:
                lines.append(f'?? {name}')
            elif path not in current:
                lines.append(f' D {name}')
            elif path.read_bytes() != self.snapshot[path]:
                lines.append(f' M {name}')
        return ''.join(f'{line}\n' for line in lines)
