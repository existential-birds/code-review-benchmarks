"""ReviewBench tasks as this suite rewrites them, from stand-in exporter output."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import tomllib

import pytest
import yaml

from harbor.cli.config_sources import load_config_source
from harbor.job_plan import JobPlan
from harbor.models.job.config import JobConfig

from benchmarks.daydream.build import BUILD, CASE_DIR, CODEX_LOGIN_DIR
from benchmarks.pi.verifier import JUDGE_AUTH_DIR, MODEL
from benchmarks.reviewbench import convert

ROOT = Path(__file__).resolve().parents[1]
BASE, HEAD = 'a' * 40, 'b' * 40


def exported_task(task: Path, name: str) -> Path:
    """The parts of one task ReviewBench's export.ts writes that this suite reads or changes."""
    pr = {'repo': 'https://github.com/owner/project', 'pr_number': 7, 'base': BASE, 'head': HEAD,
          'title': 'Fix pagination', 'body': 'Stops at the last page.', 'nwo': 'owner/project'}
    for path, text in {
        'environment/Dockerfile': 'FROM node@sha256:x\nRUN apt-get install -y git\n'
            'RUN mkdir -p /work/repo /work/pr /work/out\nWORKDIR /work/repo\nRUN git fetch origin base head\n'
            'COPY pr.json /work/pr/pr.json\n',
        'environment/pr.json': json.dumps(pr),
        'task.toml': f'schema_version = "1.3"\n[task]\nname = "reviewbench/{name}"\n'
            'keywords = ["code-review", "reviewbench"]\n\n[metadata]\npr_key = "x"\n\n'
            '[agent]\ntimeout_sec = 900\n\n[verifier]\ntimeout_sec = 7200\n\n'
            f'[verifier.env]\nPI_CODING_AGENT_DIR = "${{{convert.JUDGE_DIR_VARIABLE}}}"\n',
        'tests/test.sh': '#!/bin/bash\nexec node scripts/harbor/verify.ts /tests/task.json\n',
        f'tests/golden/{name}.json': '{"findings": [{"message": "verifier only golden finding"}]}',
        'solution/findings.json': '{"findings": [{"message": "verifier only golden finding"}]}',
    }.items():
        (task / path).parent.mkdir(parents=True, exist_ok=True)
        (task / path).write_text(text)
    return task


def test_task_runs_the_pinned_daydream_and_points_the_judge_at_its_login(tmp_path, pinned_wheel):
    from benchmarks.daydream import build
    task = exported_task(tmp_path / 'task', 'owner_project_7-bbbbbbbb')
    convert.configure_task(task, build.reviewer_image())
    lines = (task / 'environment/Dockerfile').read_text().splitlines()
    # Daydream's shared layers come before the per-task checkout; the frozen
    # comparison comes after the checkout it records.
    install, mkdir = lines.index('RUN apt-get install -y git'), lines.index('RUN mkdir -p /work/repo /work/pr /work/out')
    assert any(line.startswith('COPY --from=ghcr.io/astral-sh/uv') for line in lines[install:mkdir])
    assert lines[-3:] == ['COPY pr.json /work/pr/pr.json', f'COPY daydream-case/ {CASE_DIR}/',
                          'RUN python -I -m benchmarks.daydream.entrypoint --freeze-comparison']
    case = json.loads((task / 'environment/daydream-case/case.json').read_text())
    assert case == {'format': 'reviewbench', 'repository': '/work/repo', 'findings': '/work/out/findings.json',
                    'base': BASE, 'pr': {'repo': 'https://github.com/owner/project', 'pr_number': 7,
                                         'base': BASE, 'head': HEAD},
                    'description': 'Fix pagination\n\nStops at the last page.'}
    identity = json.loads((task / 'environment/daydream-case/build.json').read_text())
    assert {key: identity[key] for key in BUILD} == BUILD
    assert (task / 'environment' / BUILD['wheel_name']).read_bytes() == pinned_wheel.read_bytes()
    compose = yaml.safe_load((task / 'environment/docker-compose.yaml').read_text())
    assert [volume['target'] for volume in compose['services']['main']['volumes']] == [f'{CODEX_LOGIN_DIR}/auth.json']
    config = tomllib.loads((task / 'task.toml').read_text())
    assert config['verifier']['env'] == {'PI_CODING_AGENT_DIR': JUDGE_AUTH_DIR}
    assert config['metadata']['daydream_wheel_sha256'] == BUILD['wheel_sha256']
    assert config['task']['keywords'] == ['code-review', 'reviewbench', 'daydream']
    models = json.loads((task / 'tests/pi-models.json').read_text())
    assert [model['id'] for model in models['providers']['openai-codex']['models']] == [MODEL]
    assert f'cp /tests/pi-models.json {JUDGE_AUTH_DIR}/models.json' in (task / 'tests/test.sh').read_text()
    reviewer_files = ''.join(path.read_text(errors='ignore') for path in (task / 'environment').rglob('*')
                             if path.is_file())
    assert 'verifier only golden finding' not in reviewer_files

    changed = exported_task(tmp_path / 'changed', 'owner_project_8-bbbbbbbb')
    (changed / 'environment/Dockerfile').write_text('FROM node@sha256:x\n')
    with pytest.raises(ValueError, match='Unexpected ReviewBench Dockerfile'):
        convert.configure_task(changed, build.reviewer_image())


def test_catalog_keeps_selected_tasks_and_drops_the_exporters_stale_manifest(tmp_path, monkeypatch, pinned_wheel,
                                                                              candidate_build):
    names = ['owner_a_1-aaaaaaaa', 'owner_b_2-bbbbbbbb', 'owner_c_3-cccccccc']
    splits = []
    def export(upstream, destination, split):
        splits.append(split)
        for name in names:
            exported_task(destination / name, name)
        (destination / 'dataset.toml').write_text('[[tasks]]\n')
        (destination / 'metric.py').write_text('EXPECTED_TASKS = {}\n')
    monkeypatch.setattr(convert, 'check_source', lambda upstream: None)
    monkeypatch.setattr(convert, 'export', export)
    output = tmp_path / 'catalog'
    with pytest.raises(ValueError, match="Unknown ReviewBench task names in the test set: \\['owner_z_9-zzzzzzzz'\\]"):
        convert.generate_catalog(tmp_path / 'upstream', output, split='test', task_names=['owner_z_9-zzzzzzzz'])
    assert not output.exists()
    tasks = convert.generate_catalog(tmp_path / 'upstream', output, split='test', task_names=[names[1]],
                                     build_file=candidate_build)
    assert splits == ['test', 'test']
    assert tasks == [output / names[1]]
    assert sorted(path.name for path in output.iterdir()) == ['catalog.json', names[1]]
    catalog = json.loads((output / 'catalog.json').read_text())
    assert catalog['tasks'] == [names[1]] and catalog['judge'] == f'openai-codex/{MODEL}'
    assert catalog['reviewbench_revision'] == convert.REVIEWBENCH_REVISION
    # A candidate catalog says so, and so does each task, so its scores can't pass for the pin's.
    assert catalog['daydream']['candidate'] is True and catalog['daydream']['source_commit'] == 'c' * 40
    metadata = tomllib.loads((tasks[0] / 'task.toml').read_text())['metadata']
    assert metadata['daydream_candidate'] is True
    assert (tasks[0] / 'environment/daydream-case/build.json').read_text().count('"candidate": true') == 1
    with pytest.raises(FileExistsError, match='Refusing to replace'):
        convert.generate_catalog(tmp_path / 'upstream', output)


def test_dev_split_is_the_full_set_less_the_exporters_test_set(tmp_path, monkeypatch, pinned_wheel):
    exported = {'full': ['owner_a_1-aaaaaaaa', 'owner_b_2-bbbbbbbb', 'owner_c_3-cccccccc'],
                'test': ['owner_b_2-bbbbbbbb']}
    def export(upstream, destination, split):
        for name in exported[split]:
            exported_task(destination / name, name)
        (destination / 'dataset.toml').write_text('[[tasks]]\n')
        (destination / 'metric.py').write_text('EXPECTED_TASKS = {}\n')
    monkeypatch.setattr(convert, 'check_source', lambda upstream: None)
    monkeypatch.setattr(convert, 'export', export)
    names = {split: [task.name for task in convert.generate_catalog(tmp_path / 'upstream', tmp_path / split, split=split)]
             for split in ('full', 'dev', 'test')}
    assert names == {'full': exported['full'], 'dev': ['owner_a_1-aaaaaaaa', 'owner_c_3-cccccccc'],
                     'test': exported['test']}
    assert sorted(path.name for path in (tmp_path / 'dev').iterdir()) == ['catalog.json', *names['dev']]
    assert json.loads((tmp_path / 'dev/catalog.json').read_text())['split'] == 'dev'
    with pytest.raises(ValueError, match="Unknown ReviewBench task names in the dev set: \\['owner_b_2-bbbbbbbb'\\]"):
        convert.generate_catalog(tmp_path / 'upstream', tmp_path / 'refused', split='dev',
                                 task_names=['owner_b_2-bbbbbbbb'])
    assert not (tmp_path / 'refused').exists()


@pytest.mark.parametrize('problem',['revision', 'dirty', 'no-npm-ci', 'metric', None])
def test_source_must_be_the_clean_pinned_checkout_with_its_exporter_installed(tmp_path, monkeypatch, problem):
    upstream = tmp_path / 'ReviewBench'
    if problem != 'no-npm-ci':
        (upstream / 'node_modules/tsx').mkdir(parents=True)
    metric = (ROOT / 'benchmarks/reviewbench/upstream/metric.py').read_text()
    (upstream / 'scripts/harbor').mkdir(parents=True)
    (upstream / 'scripts/harbor/metric.py').write_text(metric + ('# changed\n' if problem == 'metric' else ''))
    head = 'c' * 40 if problem == 'revision' else convert.REVIEWBENCH_REVISION
    monkeypatch.setattr(convert, 'subprocess', SimpleNamespace(
        run=lambda command, **_: SimpleNamespace(returncode=0, stdout=head + '\n'),
        check_output=lambda command, **_: ' M corpus/manifest.json\n' if problem == 'dirty' else ''))
    if problem is None:
        convert.check_source(upstream)
        return
    message = {'revision': 'Expected ReviewBench revision', 'dirty': 'clean ReviewBench checkout',
               'no-npm-ci': 'Run npm ci', 'metric': 'metric differs'}[problem]
    with pytest.raises(ValueError, match=message):
        convert.check_source(upstream)


def test_experiment_settings_reach_every_trial(tmp_path):
    for name in ('owner_a_1-aaaaaaaa', 'owner_b_2-bbbbbbbb'):
        task = exported_task(tmp_path / 'catalog' / name, name)
        (task / 'instruction.md').write_text('Review the pull request.\n')
    plans = {}
    for experiment in ('reviewbench', 'reviewbench-oracle'):
        config = JobConfig.model_validate({**load_config_source(ROOT / f'experiments/{experiment}.yaml'),
            'jobs_dir': str(tmp_path / 'jobs'), 'datasets': [{'path': str(tmp_path / 'catalog')}]})
        plans[experiment] = asyncio.run(JobPlan.from_config(config)).trial_configs
    trials = plans['reviewbench']
    assert len(trials) == 2
    for trial in trials:
        assert trial.agent.import_path == 'benchmarks.daydream.agent:DaydreamAgent'
        assert trial.agent.override_timeout_sec == 5400 and trial.agent.kwargs['capture_data'] is True
        assert trial.environment.override_cpus == 4 and trial.environment.override_memory_mb == 8192
        assert trial.environment.kwargs['auto_mounting'] is True
        assert trial.verifier.import_path == 'benchmarks.daydream.verifier:DaydreamVerifier'
    for trial in [*trials, *plans['reviewbench-oracle']]:
        # The judge's login must outlast the tasks' verifier timeout plus Pi's
        # five-minute refresh threshold, or scoring can fail midway.
        assert trial.verifier.kwargs['min_judge_validity_sec'] >= 7200 + 300
    assert {trial.verifier.import_path for trial in plans['reviewbench-oracle']} == {'benchmarks.pi.verifier:PiJudgeVerifier'}
