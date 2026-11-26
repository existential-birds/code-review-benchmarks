"""Load the loop's settings from loop/settings.toml, with a caller's overrides.

The file holds every setting's default and reason. The reviewer, judge and proposer
slots (#81) are its ``[reviewer]``, ``[judge]`` and ``[proposer]`` tables, checked by
``loop.slots``. Values derived from the noise measurement (#45, #82) sit in its
``[derived]`` table with the reviewer and judge slots they were measured under; a file
without them, or measured under other slots than the configured ones, refuses to hand
one out rather than guess. A value given as an override is the caller's own and is
handed out as given.
"""
from __future__ import annotations

import math
from pathlib import Path
import tomllib

from loop import slots

FILE = Path(__file__).with_name('settings.toml')
NOISE_ISSUE = 'https://github.com/existential-birds/code-review-benchmarks/issues/82'
SETTINGS = {
    'climb_benchmark': str, 'primary_metric': str, 'climb_set_size': int, 'check_set_size': int,
    'target_gain': float, 'accept_z': float, 'rounds_max': int, 'futility_stop': bool,
    'max_cost_ratio': float, 'max_findings_ratio': float, 'max_diff_files': int, 'max_diff_lines': int,
    'gate_timeout_seconds': int, 'concurrency': int, 'iteration_max_wall_hours': float,
    'iteration_max_cost_usd_equiv': float, 'run_max_iterations': int, 'run_max_cost_usd_equiv': float,
    'run_max_wall_hours': float, 'plateau_iterations': int, 'max_unmerged_winners': int,
    'auto_open_pr': bool, 'holdout_rounds': int, 'holdout_rule': str, 'holdout_max_uses': int,
    'canary_cases': int, 'canary_every': int, 'baseline_max_age_days': float, 'keep_rejected_jobs': int,
    'min_matched_fraction': float, 'max_proposal_attempts': int, 'invalid_streak_stop': int,
    'limit_backoff_minutes': float, 'aacr_in_climb': bool, 'trial_retries': int,
}
# Each slot's table and the function that checks it.
SLOTS = {'reviewer': slots.reviewer, 'judge': slots.judge, 'proposer': slots.proposer}
DERIVED = {'sigma': float, 'rounds': int, 'precision_margin': float,
           'wrong_findings_margin': float, 'incomplete_margin': float}
# Which reviewer and judge the [derived] values were measured under.
MEASURED = {'reviewer': dict, 'judge': str}


def _valid(kind: type, value: object) -> bool:
    """TOML integers count as floats; booleans never count as numbers."""
    if isinstance(value, bool) != (kind is bool):
        return False
    return isinstance(value, (int, float) if kind is float else kind)


class Settings:
    def __init__(self, values: dict[str, object], measured: dict[str, object] | None = None,
                 overridden: frozenset[str] = frozenset()):
        self._values = values
        self._measured = measured or {}
        self._overridden = overridden

    def __getitem__(self, name: str):
        if name == 'accept_threshold':
            return self.accept_threshold()
        if name in SLOTS:
            return dict(self._values[name])
        if name not in SETTINGS and name not in DERIVED:
            raise KeyError(f'Unknown loop setting: {name}')
        if name in DERIVED and name not in self._overridden:
            if name not in self._values:
                raise LookupError(f'{name} is unset until the noise measurement derives it: {NOISE_ISSUE}')
            self.check_measured()
        return self._values[name]

    def check_measured(self) -> None:
        """Refuse [derived] values measured under another reviewer or judge slot than the configured ones."""
        if set(self._measured) != set(MEASURED):
            raise LookupError('[derived] names no reviewer and judge it was measured under; measure the noise '
                              f'again under the configured slots: {NOISE_ISSUE}')
        try:
            slots.check_measured(self._measured, self['reviewer'], self['judge'], 'The [derived] noise measurement')
        except ValueError as error:
            raise LookupError(f'{error}: {NOISE_ISSUE}') from None

    def accept_threshold(self, rounds: int | None = None) -> float:
        """The gain two R-round means of one build reach by chance about 1 time in 40."""
        rounds = self['rounds'] if rounds is None else rounds
        return self['accept_z'] * self['sigma'] * math.sqrt(2 / rounds)


def load(path: Path = FILE, **overrides: object) -> Settings:
    """Read the settings file, apply overrides, and reject unknown, missing or mistyped values."""
    data = tomllib.loads(Path(path).read_text())
    derived = data.pop('derived', {})
    if not isinstance(derived, dict):
        raise ValueError('[derived] must be a table')
    measured = {name: derived.pop(name) for name in MEASURED if name in derived}
    if wrong := sorted(name for name, value in measured.items() if not isinstance(value, MEASURED[name])):
        raise ValueError(f'[derived] records of the wrong type: {wrong}')
    if 'reviewer' in measured:
        measured['reviewer'] = slots.reviewer(measured['reviewer'])
    for name, check in SLOTS.items():
        if name in data:
            data[name] = check(data[name])
        if name in overrides:
            overrides[name] = check(overrides[name])
    for source, names, known in (('settings', data, SETTINGS | SLOTS), ('[derived]', derived, DERIVED),
                                 ('overrides', overrides, SETTINGS | SLOTS | DERIVED)):
        unknown = sorted(set(names) - set(known))
        if unknown:
            raise ValueError(f'Unknown loop settings in {source}: {unknown}')
    missing = sorted((set(SETTINGS) | set(SLOTS)) - set(data))
    if missing:
        raise ValueError(f'Missing loop settings: {missing}')
    values = data | derived | overrides
    kinds = SETTINGS | DERIVED
    wrong = sorted(name for name, value in values.items() if name not in SLOTS and not _valid(kinds[name], value))
    if wrong:
        raise ValueError(f'Loop settings of the wrong type: {wrong}')
    return Settings(values, measured, frozenset(overrides))
