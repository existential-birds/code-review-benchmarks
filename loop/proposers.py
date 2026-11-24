"""One small adapter per proposer CLI: its pinned install, its command and its login (#81).

``loop.propose`` runs the ``[proposer]`` slot's CLI in a container of its own and never on
the host. Each adapter says how to install the CLI at a pinned version on the proposer's
image, the non-interactive command that reads the prompt on stdin, and which login it
mounts or which key it passes:

- ``codex``: ``codex exec -m <model> -c model_reasoning_effort=<effort>``, with a Codex home
  holding a config and the reviewer's Codex login (``REVIEWER_CODEX_AUTH_DIR``).
- ``agy``: ``agy --prompt=<prompt> --add-dir "$PWD" --model <model> [--effort <effort>]``, as
  Harbor's own agy agent runs it (``--add-dir`` keeps headless agy's writes in the working
  folder). A model name that carries an effort (``gemini-4-neon-xhigh``) is passed alone, and a
  slot whose effort disagrees is refused; a base name gets ``--effort``. agy keeps its macOS
  login in the Keychain, which no container reads, so the container gets a login folder of its
  own, ``AGY_LOGIN_DIR``, mounted as its ``~/.gemini`` (HOME is /tmp). Sign in there once from
  a Linux agy, for example ``docker run -it -v "$AGY_LOGIN_DIR:/tmp/.gemini" -e HOME=/tmp`` on
  the adapter's image, then ``agy``.
- ``claude``: ``claude -p --model <model>``, with ``CLAUDE_LOGIN_DIR`` as its config folder.
  On macOS Claude Code keeps its login in the Keychain too, so sign in once with that folder
  as ``CLAUDE_CONFIG_DIR`` in the adapter's image. Claude Code takes no effort here, so the
  slot's effort must be ``default``.
- ``pi``: ``pi -p --provider <provider> --model <model> --thinking <effort>``, where the slot's
  model is ``<provider>/<model id>`` and the provider's key comes from the environment by name.

``default`` for a model or an effort passes none, so the CLI's own applies, except where an
adapter needs one.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
from typing import Callable

from benchmarks.daydream import build
from loop import slots

DEFAULT = slots.DEFAULT
CODEX_CONFIG = 'model_provider = "openai"\ncli_auth_credentials_store = "file"\n'
# agy 1.5.16's Linux builds, from its installer's manifests (antigravity.google/cli/install.sh,
# manifests/linux_<arch>.json), by Debian architecture.
AGY_VERSION = '1.5.16'
AGY_BUILDS = {
    'arm64': ('https://storage.googleapis.com/antigravity-public/antigravity-cli/1.5.16-5250855658717184/'
              'linux-arm/cli_linux_arm64.tar.gz',
              '23c193151499a4986fd5fa0d8132665cc8ffbad6cc0b3ff4fd53a2cb68abb76b149a992b836d53063443cd89add335f14d8134fb02abdcecbf6e1eaef6c53d33'),
    'amd64': ('https://storage.googleapis.com/antigravity-public/antigravity-cli/1.5.16-5250855658717184/'
              'linux-x64/cli_linux_x64.tar.gz',
              '3dee9f26e298156db673f6ce2a0f5dfed5b30006e04ceb9809c68070e7b717587b33a3da0f8bc2eda45af81a50bc8b14c82b28ae5832bcfe3e7fd49397117b31'),
}
CLAUDE_VERSION = '2.1.331'
# Daydream's Pi backend knows these providers' key variables; the proposer reuses them.
PI_KEYS = {'openrouter': 'OPENROUTER_API_KEY', 'zai': 'ZAI_API_KEY', 'nous': 'NOUS_API_KEY'}
BASE = f'''FROM {build.NODE_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git ripgrep \\
    && rm -rf /var/lib/apt/lists/*
COPY --from={build.UV_IMAGE} /uv /usr/local/bin/uv
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_LINK_MODE=copy UV_NO_CACHE=1
RUN uv python install {build.PYTHON} && ln -s "$(uv python find {build.PYTHON})" /usr/local/bin/python3
'''


@dataclass(frozen=True)
class Mount:
    source: Path
    target: str
    mode: str = 'rw'


@dataclass(frozen=True)
class Launch:
    """What one attempt needs beyond the proposer's folders: mounts, variables and variables passed by name."""
    mounts: tuple[Mount, ...] = ()
    variables: tuple[tuple[str, str], ...] = ()
    passed: tuple[str, ...] = ()


@dataclass(frozen=True)
class Adapter:
    cli: str
    install: str
    command: Callable[[dict], list[str]]
    login: Callable[[dict, Path], Launch]

    @property
    def dockerfile(self) -> str:
        return BASE + self.install + 'RUN python3 --version\n'

    @property
    def image(self) -> str:
        return f'daydream-loop-proposer-{self.cli}:' + hashlib.sha256(self.dockerfile.encode()).hexdigest()[:16]


def _set(value: str) -> str | None:
    return None if value == DEFAULT else value


def _folder(variable: str, fallback: Path | None, what: str) -> Path:
    folder = os.environ.get(variable) or (str(fallback) if fallback else '')
    if not folder or not Path(folder).is_dir():
        raise ValueError(f'No {what} folder; set {variable}')
    return Path(folder)


def _codex_command(slot: dict) -> list[str]:
    model, effort = _set(slot['model']), _set(slot['effort'])
    return ['codex', 'exec', '--dangerously-bypass-approvals-and-sandbox', '--skip-git-repo-check',
            *(['-m', model] if model else []), *(['-c', f'model_reasoning_effort={effort}'] if effort else [])]


def codex_login() -> Path:
    """The reviewer's Codex login file, as the review tasks mount it."""
    folder = os.environ.get('REVIEWER_CODEX_AUTH_DIR') or os.environ.get('CODEX_HOME') or str(Path.home() / '.codex')
    login = Path(folder) / 'auth.json'
    if not login.is_file():
        raise ValueError(f'No Codex login at {login}; sign in with codex login, or set REVIEWER_CODEX_AUTH_DIR')
    return login


def _codex_login(slot: dict, scratch: Path) -> Launch:
    home = scratch / 'codex'
    home.mkdir(exist_ok=True)
    (home / 'config.toml').write_text(CODEX_CONFIG)
    return Launch(mounts=(Mount(home, '/codex'), Mount(codex_login(), '/codex/auth.json')),
                  variables=(('CODEX_HOME', '/codex'),))


def agy_effort(slot: dict) -> str | None:
    """The effort agy is given, refusing a slot whose model name says another."""
    named = next((level for level in slots.EFFORTS if slot['model'].endswith('-' + level)), None)
    effort = _set(slot['effort'])
    if named and effort and named != effort:
        raise ValueError(f"[proposer] agy model {slot['model']} names effort {named}, but the slot's effort is {effort}")
    return effort or named


def _agy_command(slot: dict) -> list[str]:
    if slot['model'] == DEFAULT:
        raise ValueError('[proposer] agy needs an explicit model')
    effort = agy_effort(slot)
    # An effort-suffixed model takes no --effort; a base name needs one to pick its tier.
    named = slot['model'].endswith(f'-{effort}') if effort else False
    flags = f"--model {slot['model']}" + (f' --effort {effort}' if effort and not named else '')
    # The prompt arrives on stdin; agy takes it as --prompt. Headless agy stops a --prompt run
    # after 5 minutes unless --print-timeout says otherwise; the loop's own timeout bounds it.
    return ['sh', '-c', 'exec agy --dangerously-skip-permissions --print-timeout 24h --add-dir "$PWD" '
            f'{flags} --prompt="$(cat)"']


def _agy_login(slot: dict, scratch: Path) -> Launch:
    folder = _folder('AGY_LOGIN_DIR', None, "agy login (the container can't read the macOS Keychain)")
    return Launch(mounts=(Mount(folder, '/tmp/.gemini'),))


def _claude_command(slot: dict) -> list[str]:
    if slot['effort'] != DEFAULT:
        raise ValueError("[proposer] the claude adapter passes no effort; set the slot's effort to 'default'")
    model = _set(slot['model'])
    return ['claude', '-p', '--dangerously-skip-permissions', *(['--model', model] if model else [])]


def _claude_login(slot: dict, scratch: Path) -> Launch:
    # The whole folder, so a refresh that replaces the credentials file by renaming works.
    folder = _folder('CLAUDE_LOGIN_DIR', None, "Claude Code login (macOS keeps it in the Keychain)")
    if not (folder / '.credentials.json').is_file():
        raise ValueError(f'No Claude Code login in {folder}; sign in with CLAUDE_CONFIG_DIR set to it')
    return Launch(mounts=(Mount(folder, '/claude'),), variables=(('CLAUDE_CONFIG_DIR', '/claude'),))


def _pi_parts(slot: dict) -> tuple[str, str]:
    provider, _, model = slot['model'].partition('/')
    if provider not in PI_KEYS or not model:
        raise ValueError(f"[proposer] a pi model is <provider>/<model id> with provider one of {sorted(PI_KEYS)}")
    return provider, model


def _pi_command(slot: dict) -> list[str]:
    provider, model = _pi_parts(slot)
    effort = _set(slot['effort'])
    return ['pi', '-p', '--provider', provider, '--model', model, *(['--thinking', effort] if effort else []),
            '--no-session']


def _pi_login(slot: dict, scratch: Path) -> Launch:
    key = PI_KEYS[_pi_parts(slot)[0]]
    if not os.environ.get(key):
        raise ValueError(f'Set {key} for the pi proposer')
    agent = scratch / 'pi'
    agent.mkdir(exist_ok=True)
    # Passed by name, so the key is in no command line.
    return Launch(mounts=(Mount(agent, '/pi'),), variables=(('PI_CODING_AGENT_DIR', '/pi'), ('PI_TELEMETRY', '0')),
                  passed=(key,))


def _agy_install() -> str:
    cases = ' '.join(f'{arch}) url={url} sum={digest} ;;' for arch, (url, digest) in AGY_BUILDS.items())
    return (f'RUN case "$(dpkg --print-architecture)" in {cases} *) exit 1 ;; esac '
            '&& curl -fsSL -o /tmp/agy.tar.gz "$url" && echo "$sum  /tmp/agy.tar.gz" | sha512sum -c - '
            '&& tar -xzf /tmp/agy.tar.gz -C /usr/local/bin antigravity && mv /usr/local/bin/antigravity /usr/local/bin/agy '
            '&& rm /tmp/agy.tar.gz\n'
            f'RUN test "$(agy --version)" = {AGY_VERSION}\n')


ADAPTERS = {
    'codex': Adapter('codex', f'RUN npm install --global --ignore-scripts @openai/codex@{build.BUILD["codex_cli"]}\n'
                              'RUN codex --version\n', _codex_command, _codex_login),
    'agy': Adapter('agy', _agy_install(), _agy_command, _agy_login),
    'claude': Adapter('claude', f'RUN npm install --global @anthropic-ai/claude-code@{CLAUDE_VERSION}\n'
                                'RUN claude --version\n', _claude_command, _claude_login),
    'pi': Adapter('pi', f'RUN npm install --global --ignore-scripts @earendil-works/pi-coding-agent@{build.BUILD["pi_cli"]}\n'
                        'RUN pi --version\n', _pi_command, _pi_login),
}


def adapter(slot: dict) -> Adapter:
    """The slot's adapter, refusing an unknown CLI or settings its command can't take."""
    if slot['cli'] not in ADAPTERS:
        raise ValueError(f"[proposer] cli must be one of {sorted(ADAPTERS)}")
    chosen = ADAPTERS[slot['cli']]
    chosen.command(slot)
    if not re.fullmatch(r'[A-Za-z0-9._:/@-]+', slot['model']):
        raise ValueError('[proposer] model must be a plain model name')
    return chosen
