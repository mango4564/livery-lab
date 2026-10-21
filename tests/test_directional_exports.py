"""Multiple reviewed views round-trip through real Tk and transactional exports."""
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from tests.tk_lifecycle import TkTestCase
from unittest.mock import patch

from review_gallery import ReviewGallery
from reviewed_exports import LABELS, SOURCE_VIEWS, export_reviewed, infer_source_view
from test_reviewed_exports import fixture


class DirectionalExportTests(TkTestCase):
    def test_multiple_logos_and_badges_in_one_region_are_independently_selectable_and_exportable(self):
        result = dict(self.result, component_categories={name: [] for name in LABELS})
        result['component_categories'].update(logo=['0', '1', '2'], 门徽=['3', '4', '5'])
        result['source_regions'] = [dict(id='R1', name='同一侧视图', source_view='左视图',
                                         bbox=[.5, .3, 110, 8], selected_ids=[])]
        groups = [('logo', ['0']), ('logo', ['1']), ('logo', ['2']),
                  ('门徽', ['3', '4']), ('门徽', ['5'])]
        result['component_candidates'] = [dict(id=f'G{i}', selected_ids=ids,
                                               category_hints=[name], recommended_categories=[name])
                                          for i, (name, ids) in enumerate(groups)]
        root = tk.Tk();root.withdraw();self.addCleanup(root.destroy)
        with patch.object(ReviewGallery, '_locator_worker', return_value=None):
            gallery = ReviewGallery(root, result, self.output)
        gallery.withdraw();self.addCleanup(gallery.destroy)
        for category, count in (('logo', 3), ('门徽', 2)):
            gallery._switch_category(category)
            self.assertEqual(len(gallery.filtered), count)
            for candidate in list(gallery.filtered):
                gallery.current_candidate = candidate
                gallery.source_view.set('左视图');gallery._confirm()
        for name in LABELS:
            if name not in ('logo', '门徽'):
                gallery._switch_category(name);gallery._absent()
        exported = export_reviewed(gallery.result, gallery.choices, self.output, patterns=gallery.patterns)
        for label, count in (('logo', 3), ('徽章', 2)):
            record = exported['manifest']['categories'][label]
            self.assertEqual(len(record['patterns']), count)
            self.assertEqual(len(set(record['files'])), count*2)
            self.assertTrue(all(p['source_region']=='R1' for p in record['patterns']))

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
                self.assertTrue(Path(filename).name.startswith(expected['source_view']+'_彩条_'))
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

    def test_unknown_direction_inherits_source_candidate_on_export(self):
        self.result['component_candidates'] = [
            {'selected_ids': ['1'], 'source_view': '底视图'},
            {'selected_ids': ['5'], 'source_view': '整体视图'}]
        for entry in self.patterns['彩条']:
            entry['source_view'] = '待确认视图'
        exported = export_reviewed(self.result, self.choices, self.output, patterns=self.patterns)
        entries = exported['manifest']['categories']['彩条']['patterns']
        self.assertEqual([e['source_view'] for e in entries], ['底视图', '整体视图'])
        self.assertIn('底视图', entries[0]['files'][0])
        # Publication must not rewrite the caller's human review state.
        self.assertEqual(self.patterns['彩条'][0]['source_view'], '待确认视图')

    def test_direction_follows_region_with_ambiguity_and_manual_override(self):
        objects = [{'id': 'body', 'bbox': [0, 0, 100, 30]},
                   {'id': 'stripe', 'bbox': [10, 10, 50, 20]},
                   {'id': 'other', 'bbox': [110, 10, 120, 20]}]
        anchors = [{'selected_ids': ['body'], 'source_view': '右视图'}]
        self.assertEqual(infer_source_view(['stripe'], objects, anchors), '右视图')
        self.assertEqual(infer_source_view(['stripe', 'other'], objects, anchors), '待确认视图')
        anchors.append({'selected_ids': ['body'], 'source_view': '左视图'})
        self.assertEqual(infer_source_view(['stripe'], objects, anchors), '待确认视图')
        anchors.append({'selected_ids': ['stripe'], 'source_view': '前视图'})
        self.assertEqual(infer_source_view(['stripe'], objects, anchors), '前视图')

    def test_ai_direction_validation_accepts_old_schema_and_rejects_bad_mapping(self):
        from vision import _validate_components, VisionError
        payload = {'categories': {n: [] for n in LABELS}, 'reason': 'test', 'confidence': .8}
        _validate_components(payload, {'G1'})
        for direction in SOURCE_VIEWS:
            _validate_components(dict(payload, source_views={'G1': direction}), {'G1'})
        for mapping in ({'G2': '左视图'}, {'G1': '../bad'}, {'G1': []}, []):
            with self.subTest(mapping=mapping), self.assertRaises(VisionError):
                _validate_components(dict(payload, source_views=mapping), {'G1'})

    def test_gallery_prefills_candidate_direction_and_preserves_human_correction(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        self.result['component_candidates'] = [
            {'id': 'left-stripe', 'selected_ids': ['1'], 'source_view': '左视图',
             'category_hints': ['彩条']}]
        gallery = ReviewGallery(root, self.result, self.output)
        gallery.withdraw()
        self.addCleanup(gallery.destroy)
        gallery._switch_category('彩条')
        self.assertEqual(gallery.source_view.get(), '左视图')
        gallery.source_view.set('右视图')
        gallery._confirm()
        gallery._preview()
        self.assertEqual(gallery.source_view.get(), '右视图')

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
        before = {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
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
        self.assertEqual(before, {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()})
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
