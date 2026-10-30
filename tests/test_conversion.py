"""AACR conversion contracts; synthetic references stay in test fixtures."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from benchmarks.aacr import convert
from benchmarks.aacr.upstream import evaluate, schema
from benchmarks.aacr.upstream.converters import aacr_bench


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
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

    def test_upstream_schema_rejects_invalid_anchor_before_render(self):
        self.record['comments'][0]['from_line'] = -1
        with self.assertRaises(schema.SchemaError):
            convert.render_task(self.record, self.root / 'task')
        self.assertFalse((self.root / 'task/task.toml').exists())

    def test_source_pin_rejects_wrong_checkout(self):
        source = self.root / 'source'
        source.mkdir()
        subprocess.run(['git', 'init', '-q', str(source)], check=True)
        with self.assertRaisesRegex(ValueError, 'AACR revision'):
            convert.generate_task(source, self.root / 'output')

    def test_source_pin_rejects_modified_checkout(self):
        source = self.root / 'source'
        source.mkdir()
        subprocess.run(['git', 'init', '-q', str(source)], check=True)
        tracked = source / 'tracked'
        tracked.write_text('original')
        subprocess.run(['git', '-C', str(source), 'add', 'tracked'], check=True)
        subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c',
                        'user.email=test@example.invalid', 'commit', '-qm', 'source'], check=True)
        revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        tracked.write_text('operator modification')
        with patch.object(convert, 'AACR_REVISION', revision):
            with self.assertRaisesRegex(ValueError, 'clean AACR checkout'):
                convert.generate_task(source, self.root / 'output')
        self.assertEqual(tracked.read_text(), 'operator modification')

    def test_prepare_context_uses_isolated_repo_with_both_revisions(self):
        origin = self.root / 'origin'
        origin.mkdir()
        subprocess.run(['git', 'init', '-q', str(origin)], check=True)
        def git(*args):
            return subprocess.check_output(['git', '-C', str(origin), *args], text=True).strip()
        (origin / 'file.php').write_text('base\n')
        git('add', 'file.php')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'base')
        base = git('rev-parse', 'HEAD')
        (origin / 'file.php').write_text('head\n')
        git('add', 'file.php')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'head')
        head = git('rev-parse', 'HEAD')
        (origin / 'operator-file').write_text('preserve')
        instance = schema.ReviewInstance('test', 'owner/name', base, head, str(origin))
        task = self.root / 'task'
        repo = convert.prepare_context(instance, task)
        self.assertEqual(repo, task / 'environment/repo')
        self.assertEqual(subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip(), head)
        self.assertEqual(subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain'], text=True), '')
        subprocess.run(['git', '-C', str(repo), 'cat-file', '-e', base + '^{commit}'], check=True)
        self.assertEqual((origin / 'operator-file').read_text(), 'preserve')
        with self.assertRaises(FileExistsError):
            convert.prepare_context(instance, task)

    def test_context_fetches_pinned_history_without_cloning_unrelated_history(self):
        origin = self.root / 'origin'
        origin.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '-C', str(origin), *args], text=True).strip()
        git('init', '-q')
        for number in range(40):
            (origin / 'file.php').write_text(str(number))
            git('add', '.')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', str(number))
        head = git('rev-parse', 'HEAD')
        for distance in (1, 35):
            with self.subTest(distance=distance):
                base = git('rev-parse', f'HEAD~{distance}')
                instance = schema.ReviewInstance('test', 'owner/name', base, head, str(origin))
                repo = convert.prepare_context(instance, self.root / f'task-{distance}')
                if distance == 1:
                    self.assertLess(int(subprocess.check_output(['git', '-C', str(repo), 'rev-list', '--all', '--count'],
                                                               text=True)), 40)
                self.assertEqual(subprocess.check_output(['git', '-C', str(repo), 'merge-base', base, head],
                                                        text=True).strip(), base)
                self.assertEqual((repo / 'file.php').read_text(), '39')

    def test_context_deepens_when_a_nearer_merge_base_is_hidden_by_shallow_history(self):
        origin = self.root / 'origin'
        origin.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '-C', str(origin), *args], text=True).strip()
        git('init', '-q')
        (origin / 'file.php').write_text('context')
        git('add', '.')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'root')
        root = git('rev-parse', 'HEAD')
        tree = git('rev-parse', 'HEAD^{tree}')
        def commit(label, *parents):
            arguments = [value for parent in parents for value in ('-p', parent)]
            return git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                       'commit-tree', tree, *arguments, '-m', label)
        nearer = commit('nearer common ancestor', root)
        tips = []
        for branch in ('base', 'head'):
            tip = nearer
            for number in range(40):
                tip = commit(f'{branch}-{number}', tip)
            tip = commit(f'{branch} merge', root, tip)
            git('update-ref', f'refs/heads/{branch}', tip)
            tips.append(tip)
        base, head = tips
        self.assertEqual(git('merge-base', base, head), nearer)
        instance = schema.ReviewInstance('test', 'owner/name', base, head, str(origin))
        repo = convert.prepare_context(instance, self.root / 'task')
        self.assertEqual(subprocess.check_output(['git', '-C', str(repo), 'merge-base', base, head],
                                                text=True).strip(), nearer)

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
        origin = self.root / 'origin'
        origin.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '-C', str(origin), *args], text=True).strip()
        git('init', '-q')
        (origin / 'file.php').write_text('base\n')
        git('add', '.')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'base')
        base = git('rev-parse', 'HEAD')
        (origin / 'file.php').write_text('head\n')
        git('add', '.')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'head')
        head = git('rev-parse', 'HEAD')
        for record in records:
            record['source_commit'] = base
            record['target_commit'] = head
        source = self.root / 'source'
        dataset = source / 'dataset/positive_samples.json'
        dataset.parent.mkdir(parents=True)
        dataset.write_text(json.dumps(records))
        subprocess.run(['git', 'init', '-q', str(source)], check=True)
        subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c',
                        'user.email=test@example.invalid', 'commit', '-qm', 'dataset'], check=True)
        revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        self.enterContext(patch.object(convert, 'AACR_REVISION', revision))
        self.enterContext(patch.object(convert, 'DATASET_SHA256', hashlib.sha256(dataset.read_bytes()).hexdigest()))
        self.enterContext(patch.object(schema.ReviewInstance, 'resolved_clone_url', property(lambda _: str(origin))))
        return source, origin

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
        source, origin = self.catalog_fixture(records)
        output = self.root / 'catalog'
        self.assertTrue(callable(getattr(convert, 'generate_catalog', None)), 'catalog conversion is missing')
        tasks = convert.generate_catalog(source, output)
        self.assertEqual([task.name for task in tasks], ['owner-project1-1', 'owner-project2-2'])
        from harbor.models.task.task import Task
        for task in tasks:
            self.assertTrue(Task.is_valid_dir(task))
            self.assertEqual(subprocess.check_output(['git', '-C', str(task / 'environment/repo'),
                             'status', '--porcelain'], text=True), '')
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
        self.assertEqual(subprocess.check_output(['git', '-C', str(origin), 'status', '--porcelain'], text=True), '')

    def test_catalog_reports_unavailable_revisions_and_keeps_other_cases(self):
        records = [dict(self.record, githubPrUrl=f'https://github.com/owner/project{number}/pull/{number}')
                   for number in (1, 2)]
        source, _ = self.catalog_fixture(records)
        records[0]['target_commit'] = '1' * 40
        dataset = source / 'dataset/positive_samples.json'
        dataset.write_text(json.dumps(records))
        subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c',
                        'user.email=test@example.invalid', 'commit', '-qm', 'unavailable'], check=True)
        revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        output = self.root / 'catalog'
        self.assertTrue(callable(getattr(convert, 'generate_catalog', None)), 'catalog conversion is missing')
        with patch.object(convert, 'AACR_REVISION', revision), patch.object(convert, 'DATASET_SHA256',
                hashlib.sha256(dataset.read_bytes()).hexdigest()):
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
                source, _ = self.catalog_fixture(records)
                if duplicate == 'instance':
                    records[1]['githubPrUrl'] = records[1]['githubPrUrl'].replace('56591', '56592')
                else:
                    records[1]['target_commit'] = '1' * 40
                dataset = source / 'dataset/positive_samples.json'
                dataset.write_text(json.dumps(records))
                subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
                subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c',
                                'user.email=test@example.invalid', 'commit', '-qm', 'duplicate'], check=True)
                revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
                with patch.object(convert, 'AACR_REVISION', revision), patch.object(convert, 'DATASET_SHA256',
                        hashlib.sha256(dataset.read_bytes()).hexdigest()):
                    with self.assertRaisesRegex(ValueError, 'duplicate.*' + duplicate):
                        convert.generate_catalog(source, self.root / 'catalog')
                self.assertFalse((self.root / 'catalog').exists())

    def test_catalog_explicit_selection_and_existing_assets_are_preserved(self):
        records = [dict(self.record, githubPrUrl=f'https://github.com/owner/project{number}/pull/{number}')
                   for number in (1, 2)]
        source, _ = self.catalog_fixture(records)
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


if __name__ == '__main__':
    unittest.main()
