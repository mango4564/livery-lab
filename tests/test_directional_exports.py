"""Multiple reviewed views round-trip through real Tk and transactional exports."""
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from review_gallery import ReviewGallery
from reviewed_exports import LABELS, export_reviewed
from test_reviewed_exports import fixture


class DirectionalExportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.result = fixture(self.root)
        self.choices = dict(self.result['component_categories'], 彩条=['1', '5'])
        self.patterns = {name: ([{'selected_ids': ids, 'source_view': '待确认视图'}] if ids else [])
                         for name, ids in self.choices.items()}
        self.patterns['彩条'] = [
            {'selected_ids': ['1'], 'source_view': '左视图'},
            {'selected_ids': ['5'], 'source_view': '右视图'}]
        self.output = self.root/'out'

    def test_separate_pairs_and_metadata_preserve_each_direction(self):
        published = export_reviewed(self.result, self.choices, self.output, patterns=self.patterns)
        folder = Path(published['output_dir'])
        record = published['manifest']['categories']['彩条']
        self.assertEqual(published['manifest']['schema_version'], 3)
        self.assertEqual(record['selected_ids'], ['1', '5'])
        for entry, expected in zip(record['patterns'], self.patterns['彩条']):
            self.assertEqual(entry['source_view'], expected['source_view'])
            self.assertEqual(entry['selected_ids'], expected['selected_ids'])
            self.assertEqual(len(entry['files']), 2)
            for filename in entry['files']:
                self.assertIn(expected['source_view'], filename)
                self.assertTrue((folder/filename).is_file())
            svg = (folder/entry['files'][0]).read_text(encoding='utf8')
            self.assertIn(expected['source_view'], svg)
            self.assertIn('source_view', svg)
        self.assertEqual(len(record['files']), 4)

    def test_same_direction_can_have_multiple_distinct_patterns(self):
        self.patterns['彩条'][1]['source_view'] = '左视图'
        result = export_reviewed(self.result, self.choices, self.output, patterns=self.patterns)
        files = result['manifest']['categories']['彩条']['files']
        self.assertEqual(len(files), len(set(files)))

    def test_invalid_or_inconsistent_patterns_never_publish(self):
        for mutation in ('unknown_view', 'duplicate', 'missing', 'unknown_id'):
            with self.subTest(mutation=mutation):
                patterns = json.loads(json.dumps(self.patterns))
                if mutation == 'unknown_view':
                    patterns['彩条'][0]['source_view'] = '../bad'
                elif mutation == 'duplicate':
                    patterns['彩条'].append(patterns['彩条'][0])
                elif mutation == 'missing':
                    patterns['彩条'].pop()
                else:
                    patterns['彩条'][0]['selected_ids'] = ['unknown']
                with self.assertRaises(ValueError):
                    export_reviewed(self.result, self.choices, self.output, patterns=patterns)
                self.assertFalse(self.output.exists())

    def test_failed_second_pattern_keeps_previous_products(self):
        first = export_reviewed(self.result, self.choices, self.output, patterns=self.patterns)
        folder = Path(first['output_dir'])
        before = {p.name: p.read_bytes() for p in folder.iterdir()}
        from reviewed_exports import rasterize
        calls = []

        def fail_right(svg, path, **kwargs):
            calls.append(path.name)
            if '右视图' in path.name:
                raise RuntimeError('second view failed')
            return rasterize(svg, path, **kwargs)

        with patch('reviewed_exports.rasterize', side_effect=fail_right):
            with self.assertRaises(RuntimeError):
                export_reviewed(self.result, self.choices, self.output, patterns=self.patterns)
        self.assertTrue(any('左视图' in name for name in calls))
        self.assertEqual(before, {p.name: p.read_bytes() for p in folder.iterdir()})
        self.assertFalse(list(self.output.glob('.review_stage_*')))

    def test_gallery_add_update_remove_restore_and_absence(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        gallery = ReviewGallery(root, self.result, self.output)
        gallery.withdraw()
        self.addCleanup(gallery.destroy)
        gallery._switch_category('彩条')
        for entry in self.patterns['彩条']:
            gallery.current_candidate = entry
            gallery.source_view.set(entry['source_view'])
            gallery._confirm()
        self.assertEqual(gallery.choices['彩条'], ['1', '5'])
        self.assertEqual(len(gallery.patterns['彩条']), 2)
        gallery.source_view.set('后视图')
        gallery._confirm()
        self.assertEqual(len(gallery.patterns['彩条']), 2)
        self.assertEqual(gallery.patterns['彩条'][1]['source_view'], '后视图')
        gallery.pattern_list.selection_set(0)
        gallery._preview_pattern()
        self.assertEqual(gallery.current_candidate['selected_ids'], ['1'])
        for category in LABELS:
            if category != '彩条':
                gallery._switch_category(category)
                gallery._absent()
        export_reviewed(self.result, gallery.choices, self.output, patterns=gallery.patterns)
        restored = ReviewGallery(root, self.result, self.output)
        restored.withdraw()
        self.addCleanup(restored.destroy)
        self.assertEqual(restored.patterns, gallery.patterns)
        restored._switch_category('彩条')
        restored.pattern_list.selection_set(0)
        restored._remove_pattern()
        self.assertEqual(restored.choices['彩条'], ['5'])
        restored.pattern_list.selection_set(0)
        restored._remove_pattern()
        self.assertNotIn('彩条', restored.choices)
        restored._absent()
        self.assertIsNone(restored.choices['彩条'])
        self.assertEqual(restored.patterns['彩条'], [])


if __name__ == '__main__':
    unittest.main()
