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
    'limit_backoff_minutes': float, 'aacr_in_climb': bool, 'proposer_command': list,
}
DERIVED = {'sigma': float, 'rounds': int, 'precision_margin': float,
           'wrong_findings_margin': float, 'incomplete_margin': float}


def _valid(kind: type, value: object) -> bool:
    """TOML integers count as floats; booleans never count as numbers; a command is a list of strings."""
    if isinstance(value, bool) != (kind is bool):
        return False
    if kind is list:
        return isinstance(value, list) and bool(value) and all(isinstance(part, str) for part in value)
    return isinstance(value, (int, float) if kind is float else kind)


class Settings:
    def __init__(self, values: dict[str, object]):
        self._values = values

    def __getitem__(self, name: str):
        if name == 'accept_threshold':
            return self.accept_threshold()
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
    if not isinstance(derived, dict):
        raise ValueError('[derived] must be a table')
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
