"""AACR conversion contracts; synthetic references stay in test fixtures."""
import json
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from benchmarks.aacr import convert
from benchmarks.aacr.upstream import evaluate, schema


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


if __name__ == '__main__':
    unittest.main()
