"""Exercise composed AACR settings through Harbor's native plan boundary."""
import asyncio
from pathlib import Path

from harbor.cli.config_sources import load_config_source, merge_config
from harbor.cli.jobs import _APPEND_CONFIG_PATHS
from harbor.job_plan import JobPlan
from harbor.models.job.config import JobConfig


def test_composed_pilot_selects_ten_cases_and_expands_independent_attempts(tmp_path):
    root = Path(__file__).resolve().parents[1]
    baseline = load_config_source(root / 'experiments/aacr-baseline.yaml')
    pilot = load_config_source(root / 'experiments/aacr-pilot.yaml')
    config = JobConfig.model_validate(merge_config(baseline, pilot, append_paths=_APPEND_CONFIG_PATHS))
    selected = config.datasets[0].task_names
    assert selected and len(selected) == len(set(selected)) == 10
    assert not config.tasks and len(config.datasets) == len(config.agents) == len(config.metrics) == 1
    for name in [*selected, 'unselected-case']:
        task = tmp_path / name
        (task / 'environment').mkdir(parents=True)
        (task / 'tests').mkdir()
        (task / 'instruction.md').write_text('Review the comparison.\n')
        (task / 'task.toml').write_text('[agent]\ntimeout_sec = 5400.0\n')
        (task / 'environment/Dockerfile').write_text('FROM scratch\n')
        (task / 'tests/test.sh').write_text('#!/bin/sh\nexit 0\n')
    config.datasets[0].path = tmp_path
    config.jobs_dir = tmp_path / 'jobs'
    plan = asyncio.run(JobPlan.from_config(config))
    assert {trial.task.path.name for trial in plan.trial_configs} == set(selected)
    assert len(plan.trial_configs) == 10
    assert config.n_attempts == config.n_concurrent_trials == 1
    assert config.retry.max_retries == 0
    assert all(trial.agent.import_path == 'benchmarks.daydream.agent:DaydreamAgent'
               and trial.agent.kwargs['capture_data'] is True
               and trial.verifier.import_path == 'benchmarks.daydream.verifier:DaydreamVerifier'
               and trial.environment.kwargs['auto_mounting'] is True for trial in plan.trial_configs)
    assert all(type(metric).__name__ == 'UvScript' for metrics in plan.metrics.values() for metric in metrics)

    config.datasets[0].task_names = selected[:1]
    config.n_attempts = config.n_concurrent_trials = 2
    attempts = asyncio.run(JobPlan.from_config(config))
    assert len(attempts.trial_configs) == 2
    assert len({trial.trial_name for trial in attempts.trial_configs}) == 2
    assert {trial.task.path.name for trial in attempts.trial_configs} == {selected[0]}
    assert config.retry.max_retries == 0
