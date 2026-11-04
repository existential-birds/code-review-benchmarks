"""AACR conversion contracts; synthetic references stay in test fixtures."""
from contextlib import redirect_stderr
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from benchmarks.aacr import convert
from benchmarks.aacr.upstream import evaluate, repo_utils, schema
from benchmarks.aacr.upstream.converters import aacr_bench
from fake_git import BASE, HEAD, FakeGitCli


class ConversionTests(unittest.TestCase):
    def fake_git(self, **settings):
        git = FakeGitCli()
        for name, value in settings.items():
            setattr(git, name, value)
        self.enterContext(patch.object(convert, 'subprocess', git))
        return git

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git = self.fake_git()
        self.record = {
            'githubPrUrl': 'https://github.com/laravel/framework/pull/56591',
            'project_main_language': 'PHP',
            'source_commit': '6d94b16fd5f4f72bb741f9b900b83179c8ab46c9',
            'target_commit': '0a5238acfc4ca99c363283bf3bb16e20905f2a39',
            'comments': [{'path': 'file.php', 'note': 'synthetic verifier-only reference',
                          'from_line': None, 'to_line': None}],
        }

    def test_raw_checksum_mismatch_without_url_stops_conversion(self):
        raw = self.root / 'raw.json'
        raw.write_text(json.dumps([self.record]))
        raw.with_suffix('.meta.json').write_text(json.dumps({'sha256': '0' * 64}))
        output = self.root / 'instances.jsonl'
        with self.assertRaisesRegex(SystemExit, 'checksum'):
            aacr_bench.convert_file(raw, output, limit=None)
        self.assertFalse(output.exists())

    def test_valid_raw_cache_without_url_remains_convertible(self):
        raw = self.root / 'raw.json'
        raw.write_text(json.dumps([self.record]))
        for metadata in ({'sha256': hashlib.sha256(raw.read_bytes()).hexdigest()}, {}):
            with self.subTest(metadata=metadata):
                raw.with_suffix('.meta.json').write_text(json.dumps(metadata))
                output = self.root / 'instances.jsonl'
                self.assertEqual(aacr_bench.convert_file(raw, output, limit=None), 1)
                instance, = schema.load_instances(output)
                self.assertEqual(instance.repo, 'laravel/framework')
                self.assertEqual(instance.reference_comments[0].text, 'synthetic verifier-only reference')

    def test_missing_raw_file_without_url_still_reports_missing_input(self):
        raw = self.root / 'raw.json'
        raw.with_suffix('.meta.json').write_text(json.dumps({'sha256': '0' * 64}))
        output = self.root / 'instances.jsonl'
        with self.assertRaisesRegex(SystemExit, 'Input file is missing'):
            aacr_bench.convert_file(raw, output, limit=None)
        self.assertFalse(output.exists())

    def test_render_uses_upstream_conversion_and_confines_references(self):
        task = self.root / 'task'
        instance = convert.render_task(self.record, task)
        loaded = schema.load_instances(task / 'tests/instance.jsonl')
        self.assertEqual(len(loaded), 1)
        reference = loaded[0].reference_comments[0]
        self.assertEqual(reference.text, 'synthetic verifier-only reference')
        self.assertEqual(reference.path, 'file.php')
        self.assertIsNone(reference.start_line)
        self.assertIsNone(reference.end_line)
        self.assertEqual(instance.instance_id, 'laravel__framework@0a5238a')
        self.assertIsNone(instance.reference_comments[0].start_line)
        metadata = tomllib.loads((task / 'task.toml').read_text())['metadata']
        self.assertEqual(metadata['base_commit'], self.record['source_commit'])
        self.assertEqual(metadata['head_commit'], self.record['target_commit'])
        self.assertEqual(metadata['instance_id'], instance.instance_id)
        self.assertEqual(metadata['pr_url'], self.record['githubPrUrl'])
        self.assertEqual(metadata['language'], 'PHP')
        for path in task.rglob('*'):
            if path.is_file() and 'tests' not in path.relative_to(task).parts:
                self.assertNotIn('synthetic verifier-only reference', path.read_text())
        # The judge's login is uploaded by the verifier, never mounted.
        self.assertEqual([path for path in (task / 'tests').rglob('*')
                          if path.name.startswith(('docker-compose', 'compose'))], [])

    def test_upstream_schema_rejects_invalid_anchor_before_render(self):
        self.record['comments'][0]['from_line'] = -1
        with self.assertRaises(schema.SchemaError):
            convert.render_task(self.record, self.root / 'task')
        self.assertFalse((self.root / 'task/task.toml').exists())

    def test_source_pin_rejects_wrong_or_modified_checkout(self):
        source = self.root / 'source'
        source.mkdir()
        self.git.checkout(source, 'c' * 40)
        with self.assertRaisesRegex(ValueError, 'AACR revision'):
            convert.generate_task(source, self.root / 'output')
        self.git.checkout(source, 'c' * 40, status=' M tracked\n')
        with patch.object(convert, 'AACR_REVISION', 'c' * 40):
            with self.assertRaisesRegex(ValueError, 'clean AACR checkout'):
                convert.generate_task(source, self.root / 'output')
        self.assertFalse((self.root / 'output').exists())

    def context(self, task='task'):
        instance = schema.ReviewInstance('test', 'owner/name', BASE, HEAD, 'https://example.invalid/owner/name')
        return convert.prepare_context(instance, self.root / task)

    def test_prepare_context_fetches_only_the_pinned_revisions_with_local_lfs(self):
        repo = self.context()
        self.assertEqual(repo, self.root / 'task/environment/repo')
        self.assertTrue(repo.is_dir())
        self.assertEqual(sorted(path.name for path in repo.parent.iterdir()), ['repo'])
        commands = self.git.commands
        # LFS filters come from the context itself, not from host or image configuration.
        self.assertLess(commands.index(('lfs', 'install', '--local', '--skip-repo')), commands.index(self.git.fetches()[0]))
        self.assertIn(('remote', 'add', 'origin', 'https://example.invalid/owner/name'), commands)
        self.assertEqual(self.git.fetches(), [('fetch', '--no-tags', '--depth=32', 'origin', BASE, HEAD)])
        self.assertIn(('checkout', '--detach', HEAD), commands)
        with self.assertRaises(FileExistsError):
            self.context()

    def test_prepare_context_rejects_a_checkout_that_is_not_the_clean_pinned_head(self):
        self.git.context_status = ' M file.php\n'
        with self.assertRaisesRegex(ValueError, 'clean pinned head'):
            self.context()
        self.assertEqual(list((self.root / 'task/environment').iterdir()), [])

    def test_context_deepens_until_no_shallow_boundary_can_hide_a_nearer_merge_base(self):
        scenarios = {
            # An older common ancestor is visible, but a shallow boundary on one side
            # could still hide a nearer one, so deepen once more.
            'hidden nearer ancestor': ([
                dict(merge_base='0' * 40, shallow=['1' * 40], exclusive=['1' * 40, '2' * 40]),
                dict(merge_base='3' * 40, shallow=['4' * 40], exclusive=['2' * 40]),
            ], ['--depth=32', '--deepen=32']),
            # No common ancestor yet: keep doubling the depth while history is cut off.
            'no visible ancestor': ([
                dict(merge_base=None, shallow=['1' * 40], exclusive=[]),
                dict(merge_base=None, shallow=['1' * 40], exclusive=[]),
                dict(merge_base='3' * 40, shallow=[], exclusive=['2' * 40]),
            ], ['--depth=32', '--deepen=32', '--deepen=64']),
        }
        for name, (rounds, fetches) in scenarios.items():
            with self.subTest(name):
                self.git = self.fake_git(rounds=rounds)
                self.context(name.replace(' ', '-'))
                self.assertEqual([args[2] for args in self.git.fetches()], fetches)
        self.git = self.fake_git(rounds=[dict(merge_base=None, shallow=[], exclusive=[])])
        with self.assertRaises(repo_utils.RepoError):
            self.context('unrelated')

    def test_oracle_supplies_valid_fixed_finding_with_unknown_lines(self):
        task = self.root / 'task'
        convert.render_task(self.record, task)
        findings = evaluate.build_target_comments_from_codex(json.loads((task / 'solution/findings.json').read_text()))
        self.assertEqual(len(findings), 1)
        self.assertIsNone(findings[0]['from_line'])
        self.assertIsNone(findings[0]['to_line'])
        self.assertEqual(findings[0]['path'], 'src/Illuminate/Database/Console/Migrations/MigrateMakeCommand.php')
        self.assertIn('platform-aware path abstraction', findings[0]['note'])
        self.assertIn('separator normalization', findings[0]['note'])


    def catalog_fixture(self, records):
        for record in records:
            record['source_commit'] = BASE
            record['target_commit'] = HEAD
        source = self.root / 'source'
        dataset = source / 'dataset/positive_samples.json'
        dataset.parent.mkdir(parents=True)
        self.git.checkout(source, 'c' * 40)
        self.enterContext(patch.object(convert, 'AACR_REVISION', 'c' * 40))
        self.enterContext(patch.object(schema.ReviewInstance, 'resolved_clone_url',
                                       property(lambda _: 'https://example.invalid/origin')))
        self.write_dataset(dataset, records)
        return source

    def write_dataset(self, dataset, records):
        dataset.write_text(json.dumps(records))
        self.enterContext(patch.object(convert, 'DATASET_SHA256', hashlib.sha256(dataset.read_bytes()).hexdigest()))

    def test_arbitrary_case_has_its_own_instruction_and_no_laravel_oracle(self):
        record = dict(self.record, githubPrUrl='https://github.com/owner/project/pull/17',
                      project_main_language='Rust')
        task = self.root / 'task'
        convert.render_task(record, task)
        instruction = (task / 'instruction.md').read_text()
        self.assertIn(record['githubPrUrl'], instruction)
        self.assertIn(record['source_commit'], instruction)
        self.assertIn(record['target_commit'], instruction)
        self.assertNotIn('Laravel', instruction)
        self.assertFalse((task / 'solution').exists())
        config = tomllib.loads((task / 'task.toml').read_text())
        self.assertEqual(config['task']['name'], 'aacr/owner-project-17')
        self.assertNotIn('oracle', config['task']['keywords'])

    def test_default_catalog_generates_every_case_with_retained_reference_accounting(self):
        records = [dict(self.record, githubPrUrl=f'https://github.com/owner/project{number}/pull/{number}')
                   for number in (2, 1)]
        source = self.catalog_fixture(records)
        output = self.root / 'catalog'
        self.assertTrue(callable(getattr(convert, 'generate_catalog', None)), 'catalog conversion is missing')
        tasks = convert.generate_catalog(source, output)
        self.assertEqual([task.name for task in tasks], ['owner-project1-1', 'owner-project2-2'])
        from harbor.models.task.task import Task
        for task in tasks:
            self.assertTrue(Task.is_valid_dir(task))
            self.assertTrue((task / 'environment/repo').is_dir())
            self.assertFalse((task / 'solution').exists())
            for path in task.rglob('*'):
                if path.is_file() and 'tests' not in path.relative_to(task).parts and '.git' not in path.parts:
                    self.assertNotIn('synthetic verifier-only reference', path.read_text())
        report = json.loads((output / 'catalog.json').read_text())
        self.assertEqual(report['total_records'], 2)
        self.assertEqual(report['selected_records'], 2)
        self.assertEqual(report['generated_tasks'], 2)
        self.assertEqual(report['reference_findings'], 2)
        self.assertEqual(report['errors'], 0)
        self.assertEqual([case['status'] for case in report['cases']], ['generated', 'generated'])
        self.assertEqual(report['cases'][0]['base_commit'], records[0]['source_commit'])
        self.assertEqual(report['cases'][0]['head_commit'], records[0]['target_commit'])

    def test_catalog_reports_unavailable_revisions_and_keeps_other_cases(self):
        records = [dict(self.record, githubPrUrl=f'https://github.com/owner/project{number}/pull/{number}')
                   for number in (1, 2)]
        source = self.catalog_fixture(records)
        records[0]['target_commit'] = '1' * 40
        self.git.unavailable.add('1' * 40)
        self.write_dataset(source / 'dataset/positive_samples.json', records)
        output = self.root / 'catalog'
        self.assertTrue(callable(getattr(convert, 'generate_catalog', None)), 'catalog conversion is missing')
        with self.assertRaisesRegex(ValueError, '1 of 2.*catalog.json'):
            convert.generate_catalog(source, output)
        report = json.loads((output / 'catalog.json').read_text())
        self.assertEqual(report['errors'], 1)
        self.assertEqual(report['generated_tasks'], 1)
        failed, completed = report['cases']
        self.assertEqual(failed['status'], 'error')
        self.assertEqual(failed['head_commit'], '1' * 40)
        self.assertIn('unavailable', failed['error'])
        self.assertEqual(completed['status'], 'generated')
        self.assertFalse((output / failed['task_name']).exists())
        self.assertTrue((output / completed['task_name']).exists())

    def test_catalog_rejects_duplicate_identities_before_writing_tasks(self):
        self.assertTrue(callable(getattr(convert, 'generate_catalog', None)), 'catalog conversion is missing')
        for duplicate in ('task', 'instance'):
            with self.subTest(duplicate=duplicate), tempfile.TemporaryDirectory() as temporary:
                self.root = Path(temporary)
                records = [dict(self.record), dict(self.record)]
                source = self.catalog_fixture(records)
                if duplicate == 'instance':
                    records[1]['githubPrUrl'] = records[1]['githubPrUrl'].replace('56591', '56592')
                else:
                    records[1]['target_commit'] = '1' * 40
                self.write_dataset(source / 'dataset/positive_samples.json', records)
                with self.assertRaisesRegex(ValueError, 'duplicate.*' + duplicate):
                    convert.generate_catalog(source, self.root / 'catalog')
                self.assertFalse((self.root / 'catalog').exists())

    def test_catalog_explicit_selection_and_existing_assets_are_preserved(self):
        records = [dict(self.record, githubPrUrl=f'https://github.com/owner/project{number}/pull/{number}')
                   for number in (1, 2)]
        source = self.catalog_fixture(records)
        output = self.root / 'catalog'
        self.assertTrue(callable(getattr(convert, 'generate_catalog', None)), 'catalog conversion is missing')
        with self.assertRaisesRegex(ValueError, 'Unknown.*missing'):
            convert.generate_catalog(source, output, task_names=['missing'])
        tasks = convert.generate_catalog(source, output, task_names=['owner-project2-2'])
        self.assertEqual([task.name for task in tasks], ['owner-project2-2'])
        before = (output / 'catalog.json').read_bytes()
        with self.assertRaises(FileExistsError):
            convert.generate_catalog(source, output)
        self.assertEqual((output / 'catalog.json').read_bytes(), before)
        self.assertFalse((output / 'owner-project1-1').exists())

    def test_dev_and_holdout_split_the_full_catalog_by_the_committed_list(self):
        records = [dict(self.record, githubPrUrl=f'https://github.com/owner/project{number}/pull/{number}')
                   for number in (1, 2, 3)]
        source = self.catalog_fixture(records)
        holdout = self.root / 'holdout.txt'
        holdout.write_text('# Rule comment\nowner-project2-2\n')
        self.enterContext(patch.object(convert, 'HOLDOUT', holdout))
        names = {split: [task.name for task in convert.generate_catalog(source, self.root / split, split=split)]
                 for split in ('full', 'dev', 'holdout')}
        self.assertEqual(names, {'full': ['owner-project1-1', 'owner-project2-2', 'owner-project3-3'],
                                 'dev': ['owner-project1-1', 'owner-project3-3'], 'holdout': ['owner-project2-2']})
        report = json.loads((self.root / 'holdout/catalog.json').read_text())
        self.assertEqual((report['split'], report['total_records'], report['selected_records']), ('holdout', 3, 1))
        with self.assertRaisesRegex(ValueError, r"Unknown AACR task names in the dev set: \['owner-project2-2'\]"):
            convert.generate_catalog(source, self.root / 'refused', split='dev', task_names=['owner-project2-2'])
        self.assertFalse((self.root / 'refused').exists())
        tasks = convert.generate_catalog(source, self.root / 'one', split='holdout', task_names=['owner-project2-2'])
        self.assertEqual([task.name for task in tasks], ['owner-project2-2'])
        holdout.write_text('owner-project2-2\nrenamed-case-9\n')
        with self.assertRaisesRegex(ValueError, r"holdout.txt lists unknown AACR cases: \['renamed-case-9'\]"):
            convert.generate_catalog(source, self.root / 'stale', split='dev')

    def test_cli_requires_the_pinned_reviewer_build_outside_the_oracle_fixture(self):
        missing = FileNotFoundError('Build the pinned Daydream first')
        for arguments in (['--upstream', 'source'], ['--upstream', 'source', '--task', 'owner-project-1']):
            with self.subTest(arguments=arguments), patch.object(sys, 'argv', ['convert', *arguments]), \
                    patch.object(convert.build, 'built_wheel', side_effect=missing), \
                    patch.object(convert, 'generate_catalog') as generate:
                with self.assertRaisesRegex(FileNotFoundError, 'Build the pinned Daydream'):
                    convert.main()
                generate.assert_not_called()

if __name__ == '__main__':
    unittest.main()
