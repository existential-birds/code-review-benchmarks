"""Load the loop's settings from loop/settings.toml, with a caller's overrides.

The file holds every setting's default and reason. Values derived from the noise
measurement (#45) sit in its [derived] table and stay unset until that lands;
asking for one before then is an error, never a guess.
"""
from __future__ import annotations

import math
from pathlib import Path
import tomllib

FILE = Path(__file__).with_name('settings.toml')
NOISE_ISSUE = 'https://github.com/existential-birds/code-review-benchmarks/issues/45'
_INT, _FLOAT, _BOOL, _STR, _ARGV = 'int', 'float', 'bool', 'str', 'argv'
SETTINGS = {
    'climb_benchmark': _STR, 'primary_metric': _STR, 'climb_set_size': _INT, 'check_set_size': _INT,
    'target_gain': _FLOAT, 'accept_z': _FLOAT, 'rounds_max': _INT, 'futility_stop': _BOOL,
    'max_cost_ratio': _FLOAT, 'max_findings_ratio': _FLOAT, 'max_diff_files': _INT, 'max_diff_lines': _INT,
    'gate_timeout_seconds': _INT, 'concurrency': _INT, 'iteration_max_wall_hours': _FLOAT,
    'iteration_max_cost_usd_equiv': _FLOAT, 'run_max_iterations': _INT, 'run_max_cost_usd_equiv': _FLOAT,
    'run_max_wall_hours': _FLOAT, 'plateau_iterations': _INT, 'max_unmerged_winners': _INT,
    'auto_open_pr': _BOOL, 'holdout_rounds': _INT, 'holdout_rule': _STR, 'holdout_max_uses': _INT,
    'canary_cases': _INT, 'canary_every': _INT, 'baseline_max_age_days': _FLOAT, 'keep_rejected_jobs': _INT,
    'min_matched_fraction': _FLOAT, 'max_proposal_attempts': _INT, 'invalid_streak_stop': _INT,
    'limit_backoff_minutes': _FLOAT, 'aacr_in_climb': _BOOL, 'proposer_command': _ARGV,
}
DERIVED = {'sigma': _FLOAT, 'rounds': _INT, 'precision_margin': _FLOAT,
           'wrong_findings_margin': _FLOAT, 'incomplete_margin': _FLOAT}


def _valid(kind: str, value: object) -> bool:
    if kind == _BOOL:
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False
    if kind == _INT:
        return isinstance(value, int)
    if kind == _FLOAT:
        return isinstance(value, (int, float))
    if kind == _STR:
        return isinstance(value, str)
    return isinstance(value, list) and bool(value) and all(isinstance(part, str) for part in value)


class Settings:
    def __init__(self, values: dict[str, object]):
        self._values = values

    def __getitem__(self, name: str):
        if name in DERIVED and name not in self._values:
            raise LookupError(f'{name} is unset until the noise measurement derives it: {NOISE_ISSUE}')
        if name not in SETTINGS and name not in DERIVED:
            raise KeyError(f'Unknown loop setting: {name}')
        return self._values[name]

    def accept_threshold(self, rounds: int | None = None) -> float:
        """The gain two R-round means of one build reach by chance about 1 time in 40."""
        rounds = self['rounds'] if rounds is None else rounds
        return self['accept_z'] * self['sigma'] * math.sqrt(2 / rounds)


def load(path: Path = FILE, **overrides: object) -> Settings:
    """Read the settings file, apply overrides, and reject unknown, missing or mistyped values."""
    data = tomllib.loads(Path(path).read_text())
    derived = data.pop('derived', {})
    for source, names, known in (('settings', data, SETTINGS), ('[derived]', derived, DERIVED),
                                 ('overrides', overrides, SETTINGS | DERIVED)):
        unknown = sorted(set(names) - set(known))
        if unknown:
            raise ValueError(f'Unknown loop settings in {source}: {unknown}')
    missing = sorted(set(SETTINGS) - set(data))
    if missing:
        raise ValueError(f'Missing loop settings: {missing}')
    values = data | derived | overrides
    kinds = SETTINGS | DERIVED
    wrong = sorted(name for name, value in values.items() if not _valid(kinds[name], value))
    if wrong:
        raise ValueError(f'Loop settings of the wrong type: {wrong}')
    return Settings(values)
