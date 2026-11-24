"""Export the pinned ReviewBench catalog as Harbor tasks reviewed by Daydream.

ReviewBench's own exporter writes each task: its frozen pull request, golden
findings and unchanged judge. This only swaps in the pinned Daydream reviewer, or
a candidate build, under the configured reviewer slot, and points the judge, with
the judge slot's model, at a Pi subscription login.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from benchmarks.daydream import build
from benchmarks.pi.verifier import JUDGE_AUTH_DIR
from loop import settings, slots

PACKAGE = Path(__file__).resolve().parent
REVIEWBENCH_REVISION = '4f04392d6bf4941ab6f8a3ea0c15b449f4458864'
# The judge's subscription token goes straight to OpenAI's API.
JUDGE_HOSTS = ('api.openai.com',)
# Exported tasks read this placeholder from the host; the judge folder is fixed instead.
JUDGE_DIR_VARIABLE = 'RB_HARBOR_PI_AGENT_DIR'


def judge_models(judge: dict) -> dict:
    """The judge's Pi model list. Pi 0.75.5, which ReviewBench's judge pins, doesn't list these
    models itself. The endpoint and API are Pi 1.6's for the openai provider; the costs are
    gpt-6.1-sol's, and only Pi's own cost estimate reads them. The context window is kept large
    on purpose: Pi 0.75.5's compaction sends max_output_tokens, which the ChatGPT login rejects."""
    return {'providers': {judge['provider']: {
        'api': 'openai-responses',
        'baseUrl': 'https://api.openai.com/v1',
        'models': [{
            'id': judge['model'], 'name': judge['model'], 'reasoning': True, 'input': ['text', 'image'],
            'contextWindow': 1050000, 'maxTokens': 8192,
            'cost': {'input': 2, 'output': 10, 'cacheRead': 0.1, 'cacheWrite': 2.5},
        }],
    }}}


def test_script(judge: dict) -> str:
    return f'''#!/bin/bash
set -euo pipefail
# The verifier uploaded the judge's login here; Pi reads its model list beside it.
cp /tests/pi-models.json {JUDGE_AUTH_DIR}/models.json
# Pi 0.75.5 predates the ChatGPT subscription login, which hands its access token to
# OpenAI's API as a plain key; give it the token in that form.
node -e '
const fs = require("fs"), path = "{JUDGE_AUTH_DIR}/auth.json";
const login = JSON.parse(fs.readFileSync(path, "utf8"))["{judge['provider']}"];
fs.writeFileSync(path, JSON.stringify({{"{judge['provider']}": {{type: "api_key", key: login.access}}}}));
'
cd /tests/judge
exec node --import tsx scripts/harbor/verify.ts /tests/task.json
'''


def check_source(upstream: Path) -> None:
    """Admit only the clean pinned checkout, with its exporter's dependencies installed."""
    revision = subprocess.run(['git', '-C', str(upstream), 'rev-parse', 'HEAD'], text=True, capture_output=True)
    if revision.returncode or revision.stdout.strip() != REVIEWBENCH_REVISION:
        raise ValueError(f'Expected ReviewBench revision {REVIEWBENCH_REVISION}')
    if subprocess.check_output(['git', '-C', str(upstream), 'status', '--porcelain'], text=True):
        raise ValueError('Expected a clean ReviewBench checkout; preserve operator changes')
    if not (upstream / 'node_modules/tsx').is_dir():
        raise ValueError('Run npm ci in the ReviewBench checkout first')
    # metric.py scores with this copy of ReviewBench's formulas.
    if (upstream / 'scripts/harbor/metric.py').read_bytes() != (PACKAGE / 'upstream/metric.py').read_bytes():
        raise ValueError("ReviewBench's metric differs from benchmarks/reviewbench/upstream/metric.py")


def export(upstream: Path, destination: Path, split: str, reviewer: dict, judge: dict) -> None:
    subprocess.run([
        'node', '--import', 'tsx', 'scripts/harbor/export.ts', '--output', str(destination),
        '--organization', 'reviewbench', '--split', split,
        '--judge-provider', judge['provider'], '--judge-model', judge['model'], '--base-image', build.NODE_IMAGE,
        # The reviewer slot's model endpoints; nothing else is reachable from a review.
        *[argument for host in slots.reviewer_access(reviewer)['hosts'] for argument in ('--agent-host', host)],
        *[argument for host in JUDGE_HOSTS for argument in ('--judge-host', host)],
        '--judge-env', f'PI_CODING_AGENT_DIR={JUDGE_DIR_VARIABLE}',
    ], cwd=upstream, check=True)


def configure_task(task: Path, image: tuple[str, dict[str, bytes], dict], reviewer: dict, judge: dict) -> None:
    """Install the pinned Daydream, or a candidate, into an exported task's reviewer image."""
    suffix, assets, identity = image
    environment = task / 'environment'
    pr = json.loads((environment / 'pr.json').read_text())
    title, body = pr.get('title', ''), pr.get('body', '')
    case = environment / 'daydream-case'
    case.mkdir()
    (case / 'case.json').write_text(json.dumps(dict(
        format='reviewbench', repository='/work/repo', findings='/work/out/findings.json', base=pr['base'],
        pr={key: pr[key] for key in ('repo', 'pr_number', 'base', 'head')},
        description=title + '\n\n' + body if title or body else None,
        reviewer=reviewer, reviewer_credential=slots.reviewer_access(reviewer)['key']), indent=2) + '\n')
    (case / 'build.json').write_text(json.dumps(identity, indent=2) + '\n')
    build.write_assets(environment, assets)
    # Daydream goes before the per-task checkout so every task shares its layers.
    docker = (environment / 'Dockerfile').read_text()
    anchor = 'RUN mkdir -p /work/repo /work/pr /work/out\n'
    if docker.count(anchor) != 1:
        raise ValueError(f'Unexpected ReviewBench Dockerfile: {task.name}')
    docker = docker.replace(anchor, suffix + anchor)
    docker += f'COPY daydream-case/ {build.CASE_DIR}/\nRUN python -I -m benchmarks.daydream.entrypoint --freeze-comparison\n'
    (environment / 'Dockerfile').write_text(docker)
    if compose := build.reviewer_compose(reviewer):
        (environment / 'docker-compose.yaml').write_text(compose)
    (task / 'tests/pi-models.json').write_text(json.dumps(judge_models(judge), indent=2) + '\n')
    (task / 'tests/test.sh').write_text(test_script(judge))
    config = (task / 'task.toml').read_text()
    placeholder = f'"${{{JUDGE_DIR_VARIABLE}}}"'
    if config.count(placeholder) != 1 or config.count('[metadata]\n') != 1:
        raise ValueError(f'Unexpected ReviewBench task.toml: {task.name}')
    config = config.replace(placeholder, json.dumps(JUDGE_AUTH_DIR))
    config = config.replace('keywords = ["code-review", "reviewbench"]', 'keywords = ["code-review", "reviewbench", "daydream"]')
    config = config.replace('[metadata]\n', '[metadata]\n' + build.task_metadata(identity, reviewer))
    (task / 'task.toml').write_text(config)


def generate_catalog(upstream: Path, output: Path, *, split: str = 'full',
                     task_names: list[str] | None = None, build_file: Path | None = None,
                     reviewer: dict | None = None, judge: dict | None = None) -> list[Path]:
    """Every task of the split, or the named ones, under the reviewer and judge slots, by default
    those in ``loop/settings.toml``."""
    loaded = settings.load() if reviewer is None or judge is None else None
    reviewer = slots.reviewer(reviewer if reviewer is not None else loaded['reviewer'])
    judge = slots.judge(judge if judge is not None else loaded['judge'])
    if judge['effort'] != slots.DEFAULT:
        raise ValueError("ReviewBench's pinned judge takes no reasoning effort; set the judge's effort to 'default'")
    upstream, output = Path(upstream).resolve(), Path(output).resolve()
    check_source(upstream)
    image = build.reviewer_image(build_file)
    if output.exists():
        raise FileExistsError(f'Refusing to replace generated catalog: {output}')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.reviewbench-', dir=output.parent) as temporary:
        staged = Path(temporary) / 'catalog'
        # The exporter knows only full and test; dev is the full set less the test set.
        export(upstream, staged, 'full' if split == 'dev' else split, reviewer, judge)
        # The exporter's manifest and metric hash tasks as exported; Harbor runs
        # the changed tasks from the directory, scored by this suite's metric.
        (staged / 'dataset.toml').unlink()
        (staged / 'metric.py').unlink()
        if split == 'dev':
            held_out = Path(temporary) / 'test'
            export(upstream, held_out, 'test', reviewer, judge)
            for task in held_out.iterdir():
                if task.is_dir():
                    shutil.rmtree(staged / task.name)
        names = sorted(path.name for path in staged.iterdir())
        if task_names is not None:
            unknown = set(task_names) - set(names)
            if unknown:
                raise ValueError(f'Unknown ReviewBench task names in the {split} set: {sorted(unknown)}')
            for name in set(names) - set(task_names):
                shutil.rmtree(staged / name)
            names = sorted(set(task_names))
        for name in names:
            configure_task(staged / name, image, reviewer, judge)
        (staged / 'catalog.json').write_text(json.dumps(dict(
            reviewbench_revision=REVIEWBENCH_REVISION, split=split, judge=slots.judge_name(judge),
            reviewer=reviewer, daydream=image[2], tasks=names), indent=2) + '\n')
        staged.rename(output)
    return [output / name for name in names]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', required=True, type=Path, help='Clean checkout of the pinned ReviewBench source')
    parser.add_argument('--output', type=Path, default=Path('artifacts/reviewbench/tasks'))
    parser.add_argument('--split', choices=('full', 'dev', 'test'), default='full',
                        help="ReviewBench's 219-PR full set, the 194 PRs outside its test set, or its 25-PR test set")
    parser.add_argument('--task', action='append', dest='task_names',
                        help='Exact task to generate from the split; repeat to select several')
    parser.add_argument('--build', type=Path, dest='build_file',
                        help="A candidate's build.json from benchmarks.daydream.build --candidate; omit for the pin")
    arguments = parser.parse_args()
    tasks = generate_catalog(arguments.upstream, arguments.output, split=arguments.split,
                             task_names=arguments.task_names, build_file=arguments.build_file)
    print(f'{len(tasks)} tasks in {arguments.output}')


if __name__ == '__main__':
    main()
