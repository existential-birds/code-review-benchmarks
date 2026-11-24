"""The three model roles as slots: who reviews, who judges, who proposes (#81).

Each slot is a table in ``loop/settings.toml`` that everything running that role reads:

- ``[reviewer]``: ``harness``, ``backend``, ``provider``, ``model``, ``effort``. Daydream
  is the only harness; its ``codex`` backend uses the Codex ChatGPT login, its ``pi``
  backend a provider's API key from the environment.
- ``[judge]``: ``harness``, ``provider``, ``model``, ``effort``. Pi on the ``openai``
  provider's ChatGPT subscription login is the only judge whose login the suite can hand
  over, so the model and effort are what can change.
- ``[proposer]``: ``cli``, ``model``, ``effort``, one of ``loop.propose``'s adapters.

``effort = "default"`` sends no effort, so the provider's or the harness's own applies; a
model of ``default`` does the same where the harness has one. The reviewer and judge
slots decide what a score means, so round summaries, noise figures and ledgers record
them, and the loop compares only scores made under the same two. The proposer slot only
decides who writes a change, and may change freely.
"""
from __future__ import annotations

from types import MappingProxyType

DEFAULT = 'default'
# The levels Daydream forwards to Codex's model_reasoning_effort and Pi's --thinking.
EFFORTS = ('off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
# What the judge's openai-responses API takes as reasoningEffort (Pi SDK 1.6.0); it has no 'off'.
JUDGE_EFFORTS = ('minimal', 'low', 'medium', 'high', 'xhigh', 'max')
REVIEWER_FIELDS = ('harness', 'backend', 'provider', 'model', 'effort')
JUDGE_FIELDS = ('harness', 'provider', 'model', 'effort')
PROPOSER_FIELDS = ('cli', 'model', 'effort')
# What each Daydream backend needs to reach its model from a task container: the hosts its
# agent phase may reach, and the environment variable holding its provider's key. Codex
# reaches its subscription endpoints with the mounted login instead of a key.
REVIEWER_BACKENDS = MappingProxyType({
    'codex': MappingProxyType({'openai': dict(hosts=('chatgpt.com', 'auth.openai.com'), key=None)}),
    # Daydream's Pi backend maps these providers to Pi's native key variables
    # (daydream/backends/pi.py, _PI_PROVIDER_API_KEY_ENV); only OpenRouter's host is known here.
    'pi': MappingProxyType({'openrouter': dict(hosts=('openrouter.ai',), key='OPENROUTER_API_KEY')}),
})
# Every key a reviewer backend may be given; the agent admits no other variable.
REVIEWER_KEYS = tuple(sorted({entry['key'] for providers in REVIEWER_BACKENDS.values()
                              for entry in providers.values() if entry['key']}))
# The reviewer every run made before slots existed: Daydream's defaults on Codex.
LEGACY_REVIEWER = MappingProxyType(dict(harness='daydream', backend='codex', provider='openai',
                                        model=DEFAULT, effort=DEFAULT))


def _table(value: object, fields: tuple[str, ...], name: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f'[{name}] must be a table')
    if missing := sorted(set(fields) - set(value)):
        raise ValueError(f'[{name}] lacks {missing}')
    if unknown := sorted(set(value) - set(fields)):
        raise ValueError(f'[{name}] has unknown fields {unknown}')
    if bad := sorted(key for key in fields if not isinstance(value[key], str) or not value[key].strip()
                     or value[key] != value[key].strip()):
        raise ValueError(f'[{name}] fields must be nonblank text: {bad}')
    if value['effort'] != DEFAULT and value['effort'] not in EFFORTS:
        raise ValueError(f'[{name}] effort must be {DEFAULT!r} or one of {list(EFFORTS)}')
    return {key: value[key] for key in fields}


def reviewer(value: object) -> dict[str, str]:
    """A checked ``[reviewer]`` table."""
    slot = _table(value, REVIEWER_FIELDS, 'reviewer')
    if slot['harness'] != 'daydream':
        raise ValueError("[reviewer] harness must be 'daydream', the system the loop improves")
    providers = REVIEWER_BACKENDS.get(slot['backend'])
    if providers is None:
        raise ValueError(f"[reviewer] backend must be one of {sorted(REVIEWER_BACKENDS)}")
    if slot['provider'] not in providers:
        raise ValueError(f"[reviewer] the {slot['backend']} backend takes provider {sorted(providers)}")
    if slot['backend'] == 'pi' and slot['model'] == DEFAULT:
        # Without a model, Daydream's Pi backend falls back to its own DEFAULT_PI_MODEL.
        raise ValueError('[reviewer] the pi backend needs an explicit model')
    return slot


def judge(value: object) -> dict[str, str]:
    """A checked ``[judge]`` table."""
    slot = _table(value, JUDGE_FIELDS, 'judge')
    if slot['harness'] != 'pi' or slot['provider'] != 'openai':
        raise ValueError("[judge] must be harness 'pi' and provider 'openai': the judge's login is Pi's "
                         'ChatGPT subscription login, and no other is handed to the verifier')
    if slot['model'] == DEFAULT:
        raise ValueError('[judge] needs an explicit model')
    if slot['effort'] != DEFAULT and slot['effort'] not in JUDGE_EFFORTS:
        raise ValueError(f"[judge] effort must be {DEFAULT!r} or one of {list(JUDGE_EFFORTS)}")
    return slot


def proposer(value: object) -> dict[str, str]:
    """A checked ``[proposer]`` table; ``loop.propose`` checks the CLI and its own rules."""
    return _table(value, PROPOSER_FIELDS, 'proposer')


def reviewer_access(slot: dict) -> dict:
    """The hosts the reviewer's agent phase reaches, and the variable holding its key, if any."""
    return REVIEWER_BACKENDS[slot['backend']][slot['provider']]


def judge_name(slot: dict) -> str:
    """The judge as catalogs and summaries record it: ``provider/model``, with ``@effort`` if one is set.

    Without an effort it is the name catalogs recorded before slots existed, so oracle rounds
    of the same judge stay comparable."""
    name = f"{slot['provider']}/{slot['model']}"
    return name if slot['effort'] == DEFAULT else f"{name}@{slot['effort']}"


def recorded_reviewer(identity: dict) -> dict:
    """The reviewer slot a catalog or summary identity records; one made before slots ran Codex."""
    return dict(identity.get('reviewer') or LEGACY_REVIEWER)


def check_measured(identity: dict, loaded_reviewer: dict, loaded_judge: dict, what: str) -> None:
    """Refuse scores or noise figures made under another reviewer or judge slot than the configured ones."""
    made = recorded_reviewer(identity)
    if made != dict(loaded_reviewer):
        raise ValueError(f'{what} was made under the reviewer slot {made}, not the configured {dict(loaded_reviewer)}; '
                         'measure it again under this reviewer')
    if identity.get('judge') != judge_name(loaded_judge):
        raise ValueError(f"{what} was judged by {identity.get('judge')!r}, not the configured "
                         f'{judge_name(loaded_judge)!r}; measure it again under this judge')
