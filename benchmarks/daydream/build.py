"""Build the pinned Daydream reviewer that every benchmark runs, or a candidate.

``build.json`` is the only place the Daydream version is set. Run this module to
build that commit's wheel and dependency lock into ``.cache/daydream``; converters
bake those exact bytes into each generated task::

    .venv/bin/python -m benchmarks.daydream.build ../daydream

To score an unreleased commit without touching the pin, build it as a candidate.
It goes to ``.cache/daydream-candidates/<commit>/`` beside its own ``build.json``,
which the converters' ``--build`` takes::

    .venv/bin/python -m benchmarks.daydream.build ../daydream --candidate <commit>
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from benchmarks.daydream.entrypoint import CASE_DIR, CODEX_LOGIN_DIR

PACKAGE = Path(__file__).resolve().parent
BUILD = json.loads((PACKAGE / 'build.json').read_text())
CACHE = PACKAGE.parents[1] / '.cache/daydream'
CANDIDATES = PACKAGE.parents[1] / '.cache/daydream-candidates'
LOCK_NAME = 'runtime-requirements.lock'
# Debian trixie, because Daydream needs Git 2.40 or later.
NODE_IMAGE = 'node:24.18.1-trixie-slim@sha256:ac39e4b5fcb2b1b34b20364fd58b2e898f3bb80731ee6f62a7536f9df3d6aadc'
UV_IMAGE = 'ghcr.io/astral-sh/uv:0.11.29@sha256:eb2843a1e56fd9e30c7276ce1a52cba86e64c7b385f5e3279a0e08e02dd058fc'
PYTHON = '3.14.3'
SITE_PACKAGES = '/opt/daydream/lib/python3.14/site-packages'


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def built_wheel(build_file: Path | None = None) -> tuple[dict, Path, Path]:
    """Return a build's identity, wheel and lock, refusing a wheel that differs from it.

    The pin by default; a candidate's wheel and lock sit beside its ``build.json``.
    """
    identity = BUILD if build_file is None else json.loads(Path(build_file).read_text())
    folder = CACHE if build_file is None else Path(build_file).parent
    wheel, lock = folder / identity['wheel_name'], folder / LOCK_NAME
    if not wheel.is_file() or not lock.is_file():
        raise FileNotFoundError('Build Daydream first: python -m benchmarks.daydream.build ../daydream [--candidate <commit>]')
    if _sha256(wheel) != identity['wheel_sha256']:
        raise ValueError(f'Daydream wheel SHA-256 differs from {build_file or "benchmarks/daydream/build.json"}')
    return identity, wheel, lock


def _build_commit(source: Path, commit: str, staged: Path) -> Path:
    """Build one commit's wheel and dependency lock into a fresh folder under ``staged``."""
    tree, output = staged / 'source', staged / 'build'
    tree.mkdir()
    archive = subprocess.run(['git', '-C', str(source), 'archive', commit], capture_output=True, check=True).stdout
    subprocess.run(['tar', '-x', '-C', str(tree)], input=archive, check=True)
    subprocess.run(['uv', 'build', '--quiet', '--wheel', '--out-dir', str(output)], cwd=tree, check=True)
    # The same hash-locked graph Daydream's own lockfile resolves.
    (output / LOCK_NAME).write_bytes(subprocess.run(
        ['uv', 'export', '--quiet', '--frozen', '--no-dev', '--no-emit-project',
         '--format', 'requirements-txt'], cwd=tree, capture_output=True, check=True).stdout)
    return output


def build(source: Path, cache: Path | None = None) -> Path:
    """Build the pinned commit from a Daydream clone, replacing any earlier build."""
    cache = CACHE if cache is None else cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.daydream-build-', dir=cache.parent) as temporary:
        output = _build_commit(source, BUILD['source_commit'], Path(temporary))
        wheel = output / BUILD['wheel_name']
        if not wheel.is_file() or _sha256(wheel) != BUILD['wheel_sha256']:
            raise ValueError('The built wheel differs from benchmarks/daydream/build.json')
        shutil.rmtree(cache, ignore_errors=True)
        output.rename(cache)
    return cache / BUILD['wheel_name']


def build_candidate(source: Path, commit: str, root: Path | None = None) -> Path:
    """Build any Daydream commit as a candidate, leaving the pin and its cache alone.

    Returns the candidate's ``build.json``. A rebuild of the same commit replaces it.
    """
    root = CANDIDATES if root is None else root
    # A full commit ID, so a branch that moves later can't change what was measured.
    commit = subprocess.run(['git', '-C', str(source), 'rev-parse', '--verify', commit + '^{commit}'],
                            capture_output=True, text=True, check=True).stdout.strip()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.daydream-build-', dir=root) as temporary:
        output = _build_commit(source, commit, Path(temporary))
        wheels = sorted(output.glob('daydream-*.whl'))
        if len(wheels) != 1:
            raise ValueError(f'Expected one Daydream wheel from {commit}')
        wheel = wheels[0]
        identity = {'source_commit': commit, 'version': wheel.name.split('-')[1], 'wheel_name': wheel.name,
                    'wheel_sha256': _sha256(wheel), 'codex_cli': BUILD['codex_cli'], 'candidate': True}
        (output / 'build.json').write_text(json.dumps(identity, indent=2) + '\n')
        shutil.rmtree(root / commit, ignore_errors=True)
        output.rename(root / commit)
    return root / commit / 'build.json'


def reviewer_image(build_file: Path | None = None, *, entrypoint: bool = True) -> tuple[str, dict[str, bytes], dict]:
    """Dockerfile lines and build-context files installing the pinned reviewer, or a candidate.

    The lines go after a Debian Node base's package setup and use only shared
    assets, so Docker reuses one cached layer stack for every task. Without
    ``entrypoint`` they install Daydream alone, for a verifier that imports it.
    """
    identity, wheel, lock = built_wheel(build_file)
    assets = {wheel.name: wheel.read_bytes(), 'reviewer-runtime.lock': lock.read_bytes()}
    lines = (
        f'COPY --from={UV_IMAGE} /uv /usr/local/bin/uv\n'
        'ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_LINK_MODE=copy UV_NO_CACHE=1\n'
        f'RUN uv python install {PYTHON} && uv venv --python {PYTHON} /opt/daydream\n'
        'COPY reviewer-runtime.lock /opt/reviewer-runtime.lock\n'
        f'COPY {wheel.name} /opt/{wheel.name}\n'
        'RUN uv pip install --no-cache --require-hashes --python /opt/daydream/bin/python '
        '-r /opt/reviewer-runtime.lock\n'
        f'RUN uv pip install --no-cache --no-deps --python /opt/daydream/bin/python /opt/{wheel.name} '
        '&& uv pip check --python /opt/daydream/bin/python\n'
        f'RUN npm install --global --ignore-scripts @openai/codex@{identity["codex_cli"]}\n'
        'ENV PATH="/opt/daydream/bin:${PATH}"\n'
        'RUN python --version && codex --version && python -c "import daydream"\n'
    )
    if entrypoint:
        for name in ('benchmarks/__init__.py', 'benchmarks/daydream/__init__.py', 'benchmarks/daydream/entrypoint.py'):
            assets['daydream-suite/' + name] = (PACKAGE.parents[1] / name).read_bytes()
        lines += ('COPY daydream-suite/ /opt/daydream-suite/\n'
                  f'RUN cp -r /opt/daydream-suite/benchmarks {SITE_PACKAGES}/\n')
    identity = {**identity, 'reviewer_runtime_lock_sha256': _sha256(lock), 'python': PYTHON,
                'node_image': NODE_IMAGE, 'uv_image': UV_IMAGE}
    return lines, assets, identity


def task_metadata(identity: dict) -> str:
    """The task.toml ``[metadata]`` lines naming the Daydream build a task runs."""
    lines = ''.join(f'daydream_{key} = {json.dumps(identity[key])}\n'
                    for key in ('source_commit', 'wheel_sha256', 'codex_cli', 'reviewer_runtime_lock_sha256'))
    return lines + ('daydream_candidate = true\n' if identity.get('candidate') else '')


def codex_login_compose(env_var: str = 'REVIEWER_CODEX_AUTH_DIR') -> str:
    """Mount only the host Codex login file, writable, into the reviewer container."""
    # Codex rewrites auth.json in place without a lock file, so a refresh in the
    # container reaches the host. Its namespace sandbox needs seccomp unconfined.
    return ('services:\n  main:\n    security_opt:\n      - seccomp=unconfined\n'
            '    volumes:\n      - type: bind\n'
            f'        source: ${{{env_var}:?set to your Codex login folder, usually ~/.codex}}/auth.json\n'
            f'        target: {CODEX_LOGIN_DIR}/auth.json\n        bind:\n          create_host_path: false\n')


def write_assets(directory: Path, assets: dict[str, bytes]) -> None:
    for name, contents in assets.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source', type=Path, help='Daydream clone containing the commit to build')
    parser.add_argument('--candidate', metavar='COMMIT',
                        help='Build this commit into .cache/daydream-candidates instead of building the pin')
    arguments = parser.parse_args()
    print(build_candidate(arguments.source, arguments.candidate) if arguments.candidate
          else build(arguments.source))


if __name__ == '__main__':
    main()
