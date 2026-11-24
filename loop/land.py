"""Land a winner: its draft pull request on Daydream, then, after a release, the pin bump here (#57).

    .venv/bin/python -m loop.land --run <run> --iteration <n> [--dry-run]
    .venv/bin/python -m loop.land --merged <pull request URL or number>
    .venv/bin/python -m loop.land --bump <tag>

Run it from the repository root. ``--iteration`` lands an iteration whose ledger shows
``confirmed``, and refuses any other. It works in the loop's own clone,
``.cache/loop/daydream``, never a person's, in the worktree
``.cache/loop/worktrees/land-<run>-<n>`` on the branch ``loop/<pattern>``, the proposal's
``change.pattern``:

1. Fetches Daydream and rebases the scored commit onto ``origin/main``. A conflict stops
   here for a person. If ``main`` moved since the baseline, the guard and the gate
   (``loop.guard``) run again on the rebased commit, and the body says the scored and the
   proposed commit differ by that rebase.
2. Adds a ``CHANGELOG.md`` line under ``[Unreleased]``, in Keep a Changelog's ``Changed``,
   when the change is notable: when it changes a file under ``daydream/``, the package
   Daydream ships, and so what its users get. Tests, docs and scripts alone are not. The
   line is the proposal's ``change.description``, refused if it names a benchmark or a review
   case. Then signs the commit with the person's key (``user.signingkey``), as Daydream's
   pre-push hook requires, with the person as committer when Git's global settings name
   one; no key, or a signature that fails, stops here and says what to set up.
3. Writes the pull request's body, from Daydream's own template at ``origin/main``, to
   ``loop-runs/<run>/land/<n>.md``: the hypothesis, pattern and evidence pointers; both
   builds' dev scores (every round, the means, the gain, the threshold, each guard, cost
   and time per case); the line "Confirmed on the held-out split", and no held-out number
   or case, since only the confirmation's word is read; the suite commit, the judge, the
   candidate's wheel SHA-256, the iteration's ledger line hash and the commands to score it
   again; and the answers to the template's checklist, SemVer and changelog among them.
4. Pushes the branch with Git's hooks on and opens a draft pull request, then records its
   URL in the ledger as a ``landing``. It never marks one ready, merges or enables
   auto-merge. Nothing from this repository goes into the commit: no benchmark file, name
   or dependency.

``--dry-run`` stops after step 3: the branch and the body stay local, and nothing is
pushed, opened or recorded. It is the default when the run's ``auto_open_pr`` is off.

``--merged`` reads the pull request with ``gh`` and, once merged, records its merge commit
as a ``merge`` entry in that run's ledger, so the winner no longer holds the loop back.

``--bump`` prepares the pin change as a branch here, ``pin/daydream-<version>`` in the
worktree ``.cache/loop/bump/<version>``: it builds the tag as
``python -m benchmarks.daydream.build .cache/loop/daydream --candidate <tag>`` does, writes
its commit, version, wheel and wheel SHA-256 to ``benchmarks/daydream/build.json``, commits
that, writes a body from this repository's template, and prints what a person runs next:
``make setup``, ``make test``, the pilots, the push and the pull request. It runs no pilot.

The first run on a new pin compares its baseline rounds with the last merged winner's
recorded dev scores (``pin_gap``, from ``loop.run``) and reports a gap above the noise.

Exits 0 when done, 1 when it stopped for a person (a conflict, no signing key, a guard
refusal, a pull request not merged), 2 when it refused or failed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import statistics
import subprocess
import sys

from benchmarks.daydream import build
from loop import decide, guard, ledger, rounds, settings
from loop.iterate import CLONE, WORKTREES

ROOT = Path(__file__).resolve().parents[1]
DAYDREAM = 'existential-birds/daydream'
SUITE_URL = 'https://github.com/existential-birds/code-review-benchmarks'
TEMPLATE = '.github/PULL_REQUEST_TEMPLATE.md'
SUITE_TEMPLATE = Path('.github/pull_request_template.md')
BUMPS = Path('.cache/loop/bump')
PIN = Path('benchmarks/daydream/build.json')
CONFIRMED = 'Confirmed on the held-out split.'
# The package Daydream ships: a change there changes what its users get, so it is notable.
PACKAGE = 'daydream/'
COMMENT = re.compile(r'<!--.*?-->', re.DOTALL)
SIGNING = """No signing key: Daydream's pre-push hook refuses unsigned commits. Set one up, then run this again:

    git config --global gpg.format ssh
    git config --global user.signingkey ~/.ssh/<your-key>.pub
    ssh-add ~/.ssh/<your-key>        # ssh-add -l must list it

or a GPG key with git config --global user.signingkey <key-id>. Daydream's CONTRIBUTING.md,
"SSH signing", has the details."""


class Stop(Exception):
    """Stops the landing for a person, saying what to do."""


LOOP_IDENTITY = {f'GIT_{role}_{key}': value for role in ('AUTHOR', 'COMMITTER')
                 for key, value in (('NAME', 'Daydream loop proposer'), ('EMAIL', 'proposer@daydream-loop.invalid'))}

# The steps that leave this machine, which a dry run never takes and tests replace.

def person_git(repository: Path, *arguments: str) -> str:
    """Git as the person runs it: their configuration, signing key and credentials, and hooks on, so Daydream's
    pre-push gate runs. Only a confirmed winner the guard accepted gets here, and the guard refuses a change to
    hooks, attributes or Git settings, so nothing the proposer wrote can name a driver or hook."""
    result = subprocess.run([guard.GIT, '-C', str(repository), *arguments], capture_output=True, encoding='utf-8',
                            errors='surrogateescape')
    if result.returncode:
        raise RuntimeError(f'git {" ".join(arguments)} failed: {result.stderr.strip()}')
    return result.stdout


def push(worktree: Path, branch: str) -> None:
    """Push the branch to Daydream with Git's hooks on."""
    person_git(worktree, 'push', '--quiet', '--set-upstream', 'origin', f'{branch}:{branch}')


def open_pr(worktree: Path, branch: str, title: str, body: Path) -> str:
    """Open a draft pull request and return its URL. Never ready, never merged, no auto-merge."""
    result = subprocess.run(['gh', 'pr', 'create', '--draft', '--repo', DAYDREAM, '--base', 'main', '--head', branch,
                             '--title', title, '--body-file', str(body)], cwd=worktree, capture_output=True,
                            text=True, check=False)
    if result.returncode:
        raise RuntimeError(f'gh pr create failed: {result.stderr.strip()}')
    return result.stdout.strip().splitlines()[-1]


def pr_state(pr: str) -> dict:
    """The pull request's URL, state and merge commit, from gh."""
    result = subprocess.run(['gh', 'pr', 'view', pr, '--repo', DAYDREAM, '--json', 'url,state,mergeCommit'],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f'gh pr view failed: {result.stderr.strip()}')
    view = json.loads(result.stdout)
    return dict(url=view['url'], state=view['state'], merge_commit=(view.get('mergeCommit') or {}).get('oid'))


# Landing.

def _config(repository: Path, *key: str) -> str:
    """A setting as the person's Git reads it; the loop's own Git reads no global settings."""
    try:
        return person_git(repository, 'config', '--get', *key).strip()
    except RuntimeError:
        return ''


def winner(entries: list[dict], n: int) -> dict:
    """Iteration n's entry, refusing one the ledger doesn't show confirmed, or already landed."""
    words = [entry['verdict'] for entry in entries if entry['kind'] == 'confirmation' and entry['iteration'] == n]
    if words != ['confirmed']:
        raise ValueError(f'Iteration {n} is not confirmed on held-out ({words[0] if words else "no confirmation"}), '
                         'so it is not landed')
    if landed := [entry['url'] for entry in entries if entry['kind'] == 'landing' and entry['iteration'] == n]:
        raise ValueError(f'Iteration {n} is already landed: {landed[0]}')
    return next(entry for entry in entries if entry['kind'] == 'iteration' and entry['iteration'] == n)


def rebase(clone: Path, worktree: Path, branch: str, base: str, commit: str) -> tuple[str, str]:
    """The scored commit rebased onto origin/main on the branch: (origin/main, the rebased commit)."""
    guard.git(clone, 'fetch', '--quiet', 'origin')
    main = guard.git(clone, 'rev-parse', '--verify', 'origin/main^{commit}').strip()
    guard.git(clone, 'worktree', 'prune')
    if worktree.exists():
        guard.git(clone, 'worktree', 'remove', '--force', str(worktree))
    worktree.parent.mkdir(parents=True, exist_ok=True)
    guard.git(clone, 'worktree', 'add', '--quiet', '-B', branch, str(worktree), commit)
    try:
        # The loop's own Git, which reads no global settings, so it names the committer; signing re-commits it.
        guard.git(worktree, 'rebase', '--quiet', '--onto', main, base, environment=LOOP_IDENTITY)
    except RuntimeError as error:
        try:
            guard.git(worktree, 'rebase', '--abort')
        except RuntimeError:
            pass
        raise Stop(f'{commit} does not rebase cleanly onto origin/main {main}; a person resolves it in '
                   f'{worktree} (git rebase --onto {main} {base}), then lands it by hand. {error}') from None
    return main, guard.git(worktree, 'rev-parse', 'HEAD').strip()


def changelog(text: str, line: str) -> str:
    """CHANGELOG.md with the line added under [Unreleased]'s Changed, made if absent."""
    lines = text.split('\n')
    try:
        at = next(number for number, value in enumerate(lines) if value.startswith('## [Unreleased]'))
    except StopIteration:
        raise ValueError('CHANGELOG.md has no [Unreleased] section') from None
    end = next((number for number in range(at + 1, len(lines)) if lines[number].startswith('## ')), len(lines))
    changed = next((number for number in range(at + 1, end) if lines[number].strip() == '### Changed'), None)
    if changed is None:
        lines[at + 1:at + 1] = ['', '### Changed', '', line]
    else:
        lines.insert(changed + 2 if lines[changed + 1:changed + 2] == [''] else changed + 1, line)
    return '\n'.join(lines)


def sign(clone: Path, worktree: Path) -> str:
    """Amend the commit signed, with the person as committer when global settings name one; return it."""
    identity = []
    for key in ('user.name', 'user.email'):
        if value := _config(clone, '--global', key):
            identity += ['-c', f'{key}={value}']
    try:
        person_git(worktree, *identity, 'commit', '--quiet', '--amend', '--no-edit', '--gpg-sign')
    except RuntimeError as error:
        raise Stop(f'Signing the commit failed: {error}\n\n{SIGNING}') from None
    header = guard.git(worktree, 'cat-file', 'commit', 'HEAD').split('\n\n', 1)[0]
    if not re.search(r'^gpgsig', header, re.MULTILINE):
        raise Stop(f'The commit is not signed.\n\n{SIGNING}')
    return guard.git(worktree, 'rev-parse', 'HEAD').strip()


def _number(value: object, sign: bool = False) -> str:
    return '–' if value is None else f'{value:+.4f}' if sign else f'{value:.4f}'


def scores_text(stage: str, scores: dict) -> str:
    """One stage's dev scores for both builds: every round, the means, the gain, the threshold and each guard."""
    matched = scores['matched']
    lines = [f'**{stage.capitalize()} set** (`loop/{stage}.txt`): {matched["cases"]} of {matched["of"]} cases '
             f'scored in every round of both builds; {scores["rounds"]["candidate"]} rounds each.', '',
             '| Build | Round | ' + ' | '.join(rounds.METRICS) + ' | cost per case (USD) | seconds per case |',
             '| --- | --- |' + ' --- |' * (len(rounds.METRICS) + 2)]
    for side in ('baseline', 'candidate'):
        rows = [(str(number), entry) for number, entry in enumerate(scores[side]['rounds'], 1)]
        for label, entry in [*rows, ('mean', scores[side]['mean'])]:
            figures = [_number(entry.get(key)) for key in (*rounds.METRICS, 'cost_per_case', 'seconds_per_case')]
            lines.append(f'| {side} | {label} | ' + ' | '.join(figures) + ' |')
    gains = ', '.join(f'{key} {_number(value, True)}' for key, value in scores['gain'].items())
    lines += ['', f'Gain in grounded F1: {_number(scores["gain"]["grounded_f1"], True)}, against a threshold of '
                  f'{_number(scores["needed_gain"])}. Every gain: {gains}.']
    if scores.get('guards'):
        lines += ['', '| Guard | Baseline | Candidate | Limit | Passed |', '| --- | --- | --- | --- | --- |']
        lines += [f'| {key} | {_number(guard_["baseline"])} | {_number(guard_["candidate"])} | '
                  f'{_number(guard_["limit"])} | {"yes" if guard_["passed"] else "no"} |'
                  for key, guard_ in scores['guards'].items()]
    return '\n'.join(lines)


def answer(item: str, facts: dict) -> str:
    """One checklist item of Daydream's template, checked only with its evidence, else why not."""
    low = item.lower()
    if 'template' in low:
        return f'- [x] {item}'
    if 'make check' in low:
        return (f'- [x] {item}' if facts['gate'] else
                f'- [ ] {item} (not run on this commit by the loop: a person runs it, and the pre-push hook does)')
    if 'test-audit' in low:
        return (f'- [ ] {item} (blocked: the change touches tests, so a person runs it)' if facts['tests'] else
                f'- [ ] {item} N/A: no test files changed')
    if 'semantic versioning' in low:
        return f'- [x] {item}'
    if 'changelog' in low:
        return (f'- [x] {item}' if facts['notable'] else
                f'- [ ] {item} N/A: no file under `{PACKAGE}` changed, so what users get is unchanged')
    return f'- [ ] {item} (for a person to answer before this is ready for review)'


def fill(template: str, sections: dict[str, str], facts: dict) -> str:
    """The template with its comments removed, the sections given filled and its checklist answered."""
    parts = re.split(r'^(## .*)$', COMMENT.sub('', template), flags=re.MULTILINE)
    out = [parts[0].strip()] if parts[0].strip() else []
    for heading, text in zip(parts[1::2], parts[2::2]):
        name = heading[3:].strip()
        if name in sections:
            text = sections[name]
        elif name == 'Checklist':
            text = '\n'.join(answer(line[6:].strip(), facts) if line.startswith('- [ ] ') else line
                             for line in text.strip().split('\n'))
        out.append(f'{heading}\n\n{text.strip()}')
    return '\n\n'.join(out) + '\n'


def body(template: str, run: str, n: int, start: dict, entry: dict, line_sha256: str, landed: dict) -> str:
    """The pull request's body, from Daydream's template: everything but held-out numbers and cases."""
    proposal, candidate, cost = entry['proposal'], entry['candidate'], entry.get('cost', {})
    change, scores = proposal.get('change', {}), entry['scores']
    description = change.get('description') or proposal['hypothesis']
    climb = scores['climb']
    check = f' and {_number(scores["check"]["gain"]["grounded_f1"], True)} on the check set' if 'check' in scores else ''
    scored = candidate['commit']
    rebased = (f'The scored commit `{scored}` and the proposed commit `{landed["commit"]}` differ by a rebase onto '
               f'`origin/main` at `{landed["main"]}`, which moved since the baseline `{candidate["baseline"]}`; '
               'the guard and the gate passed again on the rebased change.' if landed['moved'] else
               f'The proposed commit `{landed["commit"]}` is the scored commit `{scored}` with the changelog line and '
               'the signature added; `main` has not moved since the baseline.')
    name = f'{run}-{n}-rescore'
    build_file = f'.cache/daydream-candidates/{scored}/build.json'
    planned = climb['rounds']['planned']
    commands = '\n'.join((
        f'git clone {SUITE_URL} && cd code-review-benchmarks && git checkout {start["suite_commit"]}',
        'make setup && make reviewbench-source',
        f'.venv/bin/python -m benchmarks.daydream.build <daydream-clone> --candidate {scored}',
        f'.venv/bin/python -m loop.rounds --build pinned --cases loop/climb.txt --rounds {planned} --name {name}-base',
        f'.venv/bin/python -m loop.rounds --build {build_file} --cases loop/climb.txt --rounds {planned} --name {name}',
        f'.venv/bin/python -m loop.decide --baseline artifacts/loop/{name}-base/summary.json '
        f'--candidate artifacts/loop/{name}/summary.json --stage climb'))
    semver = (f'Compatibility: a compatible change to review behaviour in {", ".join(f"`{path}`" for path in landed["files"])}, '
              'with no change meant to a public contract (API, CLI, configuration or result format): a patch '
              'release under 0.y.z. A reviewer confirms.')
    changelog_answer = (f'Changelog: added under `[Unreleased]` → `Changed`: {landed["changelog"]}' if landed['notable']
                        else f'Changelog: N/A, since no file under `{PACKAGE}` changed.')
    gate = landed['gate']
    gate_line = (f'- `make check` passed in the guard\'s clean clone of `{gate["commit"]}` in '
                 f'{(gate.get("gate") or {}).get("seconds")} seconds, with a throwaway HOME (`loop.guard`).'
                 if gate.get('accepted') else '- `make check` was not run by the loop on this commit.')
    sections = {
        'Summary': '\n'.join((
            f'- {proposal["hypothesis"]}',
            f'- {description}',
            f'- Kept on dev with a grounded F1 gain of {_number(climb["gain"]["grounded_f1"], True)} on the climb set '
            f'(threshold {_number(climb["needed_gain"])}){check}. {CONFIRMED}')),
        'Motivation': '\n'.join((
            f'Proposed by the Daydream tuning loop from dev review evidence (run `{run}`, iteration {n}).', '',
            f'- Hypothesis: {proposal["hypothesis"]}',
            f'- Pattern: `{change.get("pattern", "unnamed")}` ({proposal["category"]})',
            f'- Prediction: {proposal["prediction"]}',
            f'- Rejected if: {proposal["rejection_criterion"]}',
            '- Evidence pointers, in the evidence pack of the baseline\'s climb-set rounds:',
            *(f'  - `{pointer}`' for pointer in proposal['evidence']))),
        'Changes': '\n'.join(('### Changed', '', f'- {description}',
                              *(f'  - `{path}`' for path in landed['files']))),
        'Test Plan': '\n'.join((
            gate_line, '',
            'Dev scores of the baseline (the pinned build) and the candidate, from the iteration\'s ledger entry. '
            'Each round is one trial per review case.', '',
            scores_text('climb', climb), *(('', scores_text('check', scores['check'])) if 'check' in scores else ()),
            '', f'Cost of the iteration: {cost.get("trials", "–")} trials, '
                f'{_number(cost.get("cost_usd_equiv"))} USD-equivalent, '
                f'{round((cost.get("reviewer_seconds") or 0) / 3600, 1)} reviewer-hours, '
                f'{round((cost.get("wall_seconds") or 0) / 3600, 1)} hours of wall time.',
            '', CONFIRMED)),
        'Additional Context': '\n'.join((
            f'- {rebased}',
            f'- Suite: {SUITE_URL} at `{start["suite_commit"]}`; judge `{start["judge"]}`.',
            f'- Candidate wheel SHA-256 (the scored commit): `{candidate.get("wheel_sha256")}`.',
            f'- Ledger: run `{run}`, iteration {n}, line SHA-256 `{line_sha256}` '
            f'(`python -m loop.ledger show {run}` prints it).',
            f'- {semver}', f'- {changelog_answer}', '', 'To score it again:', '', '```sh', commands, '```')),
    }
    tests = any(path.startswith('tests/') for path in landed['files'])
    return fill(template, sections, dict(gate=bool(gate.get('accepted')), tests=tests, notable=landed['notable']))


def land(run: str, n: int, *, dry_run: bool | None = None, root: Path = ROOT) -> dict:
    """Land iteration n of the run: its branch, body and, unless a dry run, its draft pull request."""
    folder = root / 'loop-runs' / run
    entries, lines = ledger._verified(folder)
    if not entries:
        raise ValueError(f'No ledger at {folder / "ledger.jsonl"}')
    entry = winner(entries, n)
    line_sha256 = hashlib.sha256(lines[entries.index(entry)]).hexdigest()
    loaded = settings.load(**entries[0]['settings'])
    dry_run = not loaded['auto_open_pr'] if dry_run is None else dry_run
    clone = root / CLONE
    if not (clone / '.git').exists():
        raise ValueError(f'No loop clone at {clone}, where the iteration\'s commit is')
    if not _config(clone, 'user.signingkey'):
        raise Stop(SIGNING)
    candidate, proposal = entry['candidate'], entry['proposal']
    branch = f'loop/{proposal.get("change", {}).get("pattern") or f"{run}-{n}"}'
    worktree = root / WORKTREES / f'land-{run}-{n}'
    main, commit = rebase(clone, worktree, branch, candidate['baseline'], candidate['commit'])
    moved = main != candidate['baseline']
    names, gate = None, entry.get('gate') or {}
    if moved:
        names = guard.benchmark_names()
        gate = guard.check(worktree, main, commit, loaded, names)
        if not gate['accepted']:
            raise Stop('After the rebase onto origin/main the guard refused it: ' + '; '.join(
                f'{refusal["check"]}: {refusal["detail"]}' for refusal in gate['refusals']))
    files = [path for path in guard.git(worktree, 'diff', '--name-only', '-z', *guard.DIFF, main, commit).split('\0')
             if path]
    notable = any(path.startswith(PACKAGE) for path in files)
    line = ''
    if notable:
        line = '- ' + ' '.join(proposal.get('change', {}).get('description', proposal['hypothesis']).split())
        pattern = guard.name_pattern(names if names is not None else guard.benchmark_names())
        if guard.KEYWORDS.search(line) or pattern.search(line):
            raise Stop(f'The changelog line would name a benchmark or a review case; write it by hand in {worktree} '
                       f'under [Unreleased], then sign: git commit --amend -S. The line: {line}')
        path = worktree / 'CHANGELOG.md'
        path.write_text(changelog(path.read_text(), line))
        guard.git(worktree, 'add', 'CHANGELOG.md')
    commit = sign(clone, worktree)
    template = guard.git(clone, 'show', f'origin/main:{TEMPLATE}')
    landed = dict(main=main, commit=commit, moved=moved, files=files, notable=notable,
                  changelog=line.removeprefix('- '), gate=gate)
    text = body(template, run, n, entries[0], entry, line_sha256, landed)
    body_path = folder / 'land' / f'{n}.md'
    body_path.parent.mkdir(parents=True, exist_ok=True)
    body_path.write_text(text)
    title = guard.git(worktree, 'log', '-1', '--format=%s').strip()
    result = dict(run=run, iteration=n, branch=branch, worktree=str(worktree), commit=commit, body=str(body_path),
                  title=title, dry_run=dry_run, url=None)
    if dry_run:
        print(f'Dry run: {branch} at {commit} in {worktree}, and its body in {body_path}. Nothing was pushed. To '
              f'land it: python -m loop.land --run {run} --iteration {n}', file=sys.stderr)
        return result
    push(worktree, branch)
    result['url'] = open_pr(worktree, branch, title, body_path)
    ledger.append(folder, 'landing', dict(iteration=n, url=result['url'], branch=branch, commit=commit))
    print(f'Draft pull request {result["url"]}: a person reviews, marks it ready and merges it', file=sys.stderr)
    return result


# After the merge.

def merged(pr: str, root: Path = ROOT) -> dict:
    """Record a landed pull request's merge commit in its run's ledger, clearing the winner."""
    state = pr_state(pr)
    if state['state'] != 'MERGED' or not state['merge_commit']:
        raise Stop(f'{state["url"]} is {state["state"]}, not merged')
    for ledger_file in sorted((root / 'loop-runs').glob('*/ledger.jsonl')):
        for entry in ledger.read(ledger_file.parent):
            if entry['kind'] == 'landing' and entry['url'] == state['url']:
                return ledger.append(ledger_file.parent, 'merge', dict(
                    iteration=entry['iteration'], url=state['url'], merge_commit=state['merge_commit']))
    raise ValueError(f'No run landed {state["url"]}')


def bump(tag: str, root: Path = ROOT) -> dict:
    """Prepare the pin change as a branch here, with a body from this repository's template; run no pilot."""
    clone = root / CLONE
    guard.git(clone, 'fetch', '--quiet', '--tags', 'origin')
    identity = json.loads(build.build_candidate(clone, tag, root / '.cache/daydream-candidates').read_text())
    current = json.loads((root / PIN).read_text())
    pin = {key: identity[key] for key in ('source_commit', 'version', 'wheel_name', 'wheel_sha256')}
    pin['codex_cli'] = current['codex_cli']
    version = identity['version']
    branch, worktree = f'pin/daydream-{version}', root / BUMPS / version
    guard.git(root, 'worktree', 'add', '--quiet', '-b', branch, str(worktree), 'HEAD')
    (worktree / PIN).write_text(json.dumps(pin, indent=2) + '\n')
    guard.git(worktree, 'add', str(PIN))
    guard.git(worktree, 'commit', '--quiet', '-m', f'Pin Daydream {version}')
    pilot = f'artifacts/reviewbench/pin-{version}-pilot'
    body_path = root / BUMPS / f'{version}.md'
    checks = ['make setup', 'make test',
              f'.venv/bin/python -m benchmarks.reviewbench.convert --upstream .cache/src/ReviewBench --output {pilot} '
              '--task simnaut_emdash_2 --task <small-case>',
              f'.venv/bin/harbor run -c experiments/reviewbench.yaml -p {pilot} -k 1 -n 2 -r 0 '
              f'--job-name pin-{version}-reviewbench-pilot',
              'make quickstart']
    steps = [f'cd {worktree}', *checks, f'git push --set-upstream origin {branch}',
             f'gh pr create --draft --base main --head {branch} --title {shlex.quote(f"Pin Daydream {version}")} '
             f'--body-file {body_path}']
    text = fill((root / SUITE_TEMPLATE).read_text(), {
        'Summary': '\n'.join((
            f'Pins Daydream {version} (`{pin["source_commit"]}`, wheel SHA-256 `{pin["wheel_sha256"]}`), the '
            'release holding the loop\'s merged winners. [What make test and the pilots showed.]', '',
            'Issue: Related to https://github.com/existential-birds/code-review-benchmarks/issues/57')),
        'Added': '- None.',
        'Changed': f'- `benchmarks/daydream/build.json` pins Daydream {version}, from {current["version"]}.',
        'Removed': '- None.',
        'Verification steps': '\n'.join((
            '- [ ] Ran the relevant documented repository checks and recorded the outcome below, or explained why '
            'they are not applicable.',
            '- [ ] For custom code, recorded the Harbor-first evidence required by `CONTRIBUTING.md`, or explained '
            'why it is not applicable. N/A: the pin only, no custom code.', '',
            'Verified revision: [commit SHA]', '', *(f'- `{check}` — [result]' for check in checks))),
        'Review context': '- `simnaut_emdash_2` is in ReviewBench\'s test split, so its pilot says only that it ran '
                          'and scored, with no number.',
    }, {})
    body_path.write_text(text)
    print(f'Prepared {branch} in {worktree}; nothing was run or pushed. Next:\n' + '\n'.join(
        f'    {step}' for step in steps), file=sys.stderr)
    return dict(branch=branch, worktree=str(worktree), pin=pin, body=str(body_path), steps=steps)


def pin_gap(summary_path: Path, loaded: settings.Settings, root: Path = ROOT, pin: str | None = None) -> dict | None:
    """On the first run on a new pin, its baseline rounds against the last merged winner's recorded dev scores.

    None unless a merged winner exists and no run has started on this pin yet. The baseline's
    rounds are pooled over the cases each of them scored; the winner's are as its ledger entry
    recorded them. The gap counts when it is larger than the accept threshold, the noise margin
    of two means of ``rounds`` rounds.
    """
    pin = pin or build.BUILD['source_commit']
    merges = []
    for ledger_file in sorted((root / 'loop-runs').glob('*/ledger.jsonl')):
        entries = ledger.read(ledger_file.parent)
        if entries and entries[0]['baseline_commit'] == pin:
            return None
        merges += [(entry['time'], entry, entries) for entry in entries if entry['kind'] == 'merge']
    if not merges:
        return None
    _, merge, entries = max(merges, key=lambda item: item[0])
    entry = next(item for item in entries if item['kind'] == 'iteration' and item['iteration'] == merge['iteration'])
    recorded = entry['scores']['climb']['candidate']
    summary = json.loads(Path(summary_path).read_text())
    cases = sorted(case for case, counts in summary['cases'].items() if all(count is not None for count in counts))
    measured = [figures['grounded_f1'] for figures in decide.side_rounds(summary, cases)]
    winner_rounds = [figures['grounded_f1'] for figures in recorded['rounds']]
    gap = statistics.fmean(measured) - recorded['mean']['grounded_f1']
    margin = loaded.accept_threshold()
    return dict(run=entries[0]['run'], iteration=merge['iteration'], merge_commit=merge['merge_commit'], pin=pin,
                winner_rounds=winner_rounds, winner_mean=recorded['mean']['grounded_f1'], baseline_rounds=measured,
                baseline_mean=statistics.fmean(measured), gap=gap, margin=margin, beyond_noise=abs(gap) > margin)


def gap_text(gap: dict) -> str:
    rounds_text = ', '.join(_number(value) for value in gap['baseline_rounds'])
    winner_text = ', '.join(_number(value) for value in gap['winner_rounds'])
    head = (f'The new pin {gap["pin"]} scores grounded F1 {_number(gap["baseline_mean"])} on the climb set '
            f'(rounds {rounds_text}); the winner {gap["run"]} #{gap["iteration"]}, merged as {gap["merge_commit"]}, '
            f'recorded {_number(gap["winner_mean"])} (rounds {winner_text}): a gap of {_number(gap["gap"], True)}')
    if gap['beyond_noise']:
        return f'{head}, larger than the noise margin {_number(gap["margin"])}. Find out why before trusting the run.'
    return f'{head}, within the noise margin {_number(gap["margin"])}.'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument('--iteration', type=int, help='Land this confirmed iteration of --run')
    which.add_argument('--merged', metavar='PR', help="A landed pull request's URL or number, once merged")
    which.add_argument('--bump', metavar='TAG', help='Prepare the pin change to this Daydream release here')
    parser.add_argument('--run', type=ledger._run_name, help='The run, a folder name under loop-runs/')
    parser.add_argument('--dry-run', action='store_true', default=None,
                        help="Write the branch and the body locally and push nothing (the default with auto_open_pr off)")
    arguments = parser.parse_args(argv)
    if (arguments.iteration is None) != (arguments.run is None):
        parser.error('--iteration and --run go together')
    try:
        if arguments.iteration is not None:
            result = land(arguments.run, arguments.iteration, dry_run=arguments.dry_run, root=ROOT)
        elif arguments.merged:
            result = merged(arguments.merged, root=ROOT)
        else:
            result = bump(arguments.bump, root=ROOT)
    except Stop as stop:
        print(stop, file=sys.stderr)
        return 1
    except (OSError, LookupError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
