import json
from pathlib import Path

import pytest

from benchmarks.reviewbench import convert
from loop import sets, settings, slots

ROOT = Path(__file__).resolve().parents[1]
REVIEWBENCH = ROOT / '.cache/src/ReviewBench'


def test_committed_sets_follow_the_rule_on_the_pinned_dev_split():
    if not (ROOT / f'.cache/src/.ReviewBench-{convert.REVIEWBENCH_REVISION}').exists():
        pytest.fail('Fetch the pinned ReviewBench first: make reviewbench-source')
    loaded = settings.load()
    climb, check = sets.read(sets.CLIMB), sets.read(sets.CHECK)
    dev = sets.dev_cases(REVIEWBENCH)
    assert (climb, check) == sets.select(dev, loaded['climb_set_size'], loaded['check_set_size'])
    assert len(climb) == loaded['climb_set_size'] and len(check) == loaded['check_set_size']
    assert not set(climb) & set(check) and set(climb + check) <= set(dev)


def test_a_larger_climb_set_keeps_the_smaller_one():
    names = [f'case-{number}' for number in range(30)]
    small, after_small = sets.select(names, 5, 5)
    large, _ = sets.select(names, 8, 5)
    assert large[:5] == small and large[5:8] == after_small[:3]
    with pytest.raises(ValueError, match='exceed the 30 dev cases'):
        sets.select(names, 20, 11)
    with pytest.raises(ValueError, match='cannot be negative'):
        sets.select(names, -1, 5)


def test_loader_reads_defaults_and_applies_overrides():
    loaded = settings.load()
    assert loaded['climb_benchmark'] == 'reviewbench' and loaded['auto_open_pr'] is True
    assert settings.load(auto_open_pr=False, concurrency=3)['concurrency'] == 3
    with pytest.raises(KeyError, match='Unknown loop setting: climb_size'):
        loaded['climb_size']


def test_loader_rejects_unknown_missing_and_mistyped_settings(tmp_path):
    text = settings.FILE.read_text()
    for written, overrides, message in (
            (text.replace('concurrency = 2', 'concurency = 2'), {}, r'Unknown loop settings in settings: \[.concurency.\]'),
            (text.replace('[derived.reviewer]', 'sigmaa = 0.01\n[derived.reviewer]'), {},
             r'Unknown loop settings in \[derived\]'),
            (text + 'temperature = 0\n', {}, r'\[reviewer\] has unknown fields \[.temperature.\]'),
            ('derived = 1\n' + text.split('# Derived by the noise')[0], {}, r'\[derived\] must be a table'),
            (text, {'rounds_maximum': 4}, r'Unknown loop settings in overrides'),
            (text.replace('concurrency = 2\n', ''), {}, r'Missing loop settings: \[.concurrency.\]'),
            (text, {'concurrency': True}, r'wrong type: \[.concurrency.\]'),
            (text.replace('[judge]\n', '[judges]\n'), {}, r'Unknown loop settings in settings: \[.judges.\]'),
            (text, {'reviewer': {'harness': 'daydream'}}, r'\[reviewer\] lacks'),
            (text, {'proposer': {'cli': 'agy', 'model': 'm', 'effort': 'extreme'}}, r'\[proposer\] effort must be'),
            # The judge's API has no 'off'; Pi's --thinking does.
            (text, {'judge': {'harness': 'pi', 'provider': 'openai', 'model': 'gpt-6.1-sol', 'effort': 'off'}},
             r'\[judge\] effort must be')):
        path = tmp_path / 'settings.toml'
        path.write_text(written)
        with pytest.raises(ValueError, match=message):
            settings.load(path, **overrides)


def test_derived_values_are_refused_until_measured(tmp_path):
    unmeasured = tmp_path / 'settings.toml'
    unmeasured.write_text(settings.FILE.read_text().split('[derived]')[0] + '[derived]\n')
    loaded = settings.load(unmeasured)
    for name in settings.DERIVED:
        with pytest.raises(LookupError, match=f'{name} is unset .*/issues/82'):
            loaded[name]
    for asked in (lambda: loaded.accept_threshold(3), lambda: loaded['accept_threshold']):
        with pytest.raises(LookupError, match='(sigma|rounds) is unset .*/issues/82'):
            asked()
    measured = settings.load(sigma=0.01, rounds=2)
    assert measured.accept_threshold() == measured['accept_threshold'] == pytest.approx(0.02)
    assert measured.accept_threshold(8) == pytest.approx(0.01)


def test_derived_values_measured_under_other_slots_are_refused(tmp_path):
    # The committed values measured Daydream on Codex (#45); the configured reviewer is Pi (#81).
    with pytest.raises(LookupError, match=r"reviewer slot .*'codex'.* not the configured .*'pi'.*/issues/82"):
        settings.load()['sigma']
    assert settings.load(reviewer=dict(slots.LEGACY_REVIEWER))['sigma'] > 0
    other_judge = dict(settings.load()['judge'], model='gpt-7')
    with pytest.raises(LookupError, match="judged by 'openai/gpt-6.1-sol', not the configured 'openai/gpt-7'"):
        settings.load(reviewer=dict(slots.LEGACY_REVIEWER), judge=other_judge)['rounds']
    # A file that doesn't say what it measured hands out nothing; a caller's own value is its own.
    unrecorded = tmp_path / 'settings.toml'
    unrecorded.write_text(settings.FILE.read_text().split('judge = "openai/gpt-6.1-sol"\n\n[derived.reviewer]')[0])
    with pytest.raises(LookupError, match='names no reviewer and judge'):
        settings.load(unrecorded)['sigma']
    assert settings.load(unrecorded, sigma=0.5)['sigma'] == 0.5


def test_the_committed_derived_values_are_the_noise_measurements():
    derived = json.loads((ROOT / 'loop/noise.json').read_text())['derived']
    loaded = settings.load(reviewer=dict(slots.LEGACY_REVIEWER))
    for name in settings.DERIVED:
        assert loaded[name] == derived[name]
    for rounds, threshold in derived['accept_threshold'].items():
        assert loaded.accept_threshold(int(rounds)) == pytest.approx(threshold)
