"""Offline coverage of region validation, inheritance, naming and review round trips."""
import copy
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from tests.tk_lifecycle import TkTestCase
import xml.etree.ElementTree as ET
from unittest.mock import patch

from core.source_regions import build_source_regions, object_regions, region_for_ids
from review_gallery import ReviewGallery
from reviewed_exports import LABELS, export_reviewed
from test_reviewed_exports import fixture
from vision import _validate_components, VisionError


def proposals():
    return [{'id': 'R1', 'name': '侧面区域A', 'source_view': '待确认视图', 'anchor_ids': ['G1']},
            {'id': 'R2', 'name': '后视图', 'source_view': '后视图', 'anchor_ids': ['G2']}]


class SourceRegionTests(TkTestCase):
    def setUp(self):
        # These tests exercise region review, not the independent locator worker.
        worker = patch.object(ReviewGallery, '_locator_worker', return_value=None)
        worker.start()
        self.addCleanup(worker.stop)

    def test_ai_region_schema_rejects_unknown_anchors_duplicates_and_paths(self):
        payload = {'categories': {n: [] for n in LABELS}, 'reason': '', 'confidence': .8,
                   'source_regions': proposals()}
        _validate_components(payload, {'G1', 'G2'})
        for key, value in [('anchor_ids', ['G9']), ('anchor_ids', [[]]), ('name', '../bad'),
                           ('source_view', []), ('id', []), ('name', 'x'*41)]:
            bad = copy.deepcopy(payload)
            bad['source_regions'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(VisionError):
                _validate_components(bad, {'G1', 'G2'})
        bad = copy.deepcopy(payload)
        bad['source_regions'].append(dict(bad['source_regions'][0]))
        with self.assertRaises(VisionError):
            _validate_components(bad, {'G1', 'G2'})

    def test_source_bounds_inherit_unselected_children_and_reject_ambiguity(self):
        candidates = [{'id': 'G1', 'bbox': [0, 0, 100, 30], 'member_ids': ['body']},
                      {'id': 'G2', 'bbox': [110, 0, 130, 30], 'member_ids': ['rear']}]
        regions = build_source_regions(candidates, {'source_regions': proposals()})
        objects = [{'id': 'stripe', 'bbox': [10, 5, 20, 10]},
                   {'id': 'rear', 'bbox': [110, 0, 130, 30]},
                   {'id': 'cross', 'bbox': [90, 5, 120, 10]}]
        assignments = object_regions(objects, regions)
        self.assertEqual(assignments, {'stripe': 'R1', 'rear': 'R2'})
        self.assertIsNone(region_for_ids(['stripe', 'rear'], assignments, regions))
        overlapping = dict(regions[0], id='R3')
        self.assertNotIn('stripe', object_regions(objects, regions+[overlapping]))

    def test_gallery_split_rename_export_layers_and_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            result = fixture(base)
            result['component_categories']['彩条'] = ['1', '5']
            regions = proposals()
            regions[0].update(bbox=[0, 0, 90, 8], selected_ids=['0', '1', '2', '3', '4'])
            regions[1].update(bbox=[100, 0, 110, 8], selected_ids=['5'])
            result['source_regions'] = regions
            root = tk.Tk(); root.withdraw()
            self.addCleanup(root.destroy)
            with patch('core.fs_scene.source_preview_objects', return_value=result['objects']):
                gallery = ReviewGallery(root, result, base/'out'); gallery.withdraw()
                self.addCleanup(gallery.destroy)
                gallery._edit_regions()
                dialog = next(w for w in gallery.winfo_children() if isinstance(w, tk.Toplevel))
                self.assertIn('原稿区域', dialog.title())
                dialog.destroy()
                gallery._switch_category('彩条')
                self.assertEqual(len(gallery.filtered), 2)
                gallery._confirm()
                self.assertEqual(gallery.patterns['彩条'][0]['region_name'], '侧面区域A')
                self.assertEqual(gallery.source_view.get(), '待确认视图')
                gallery.current_candidate = {'selected_ids': ['5']}
                gallery.source_view.set('后视图'); gallery._confirm()
                gallery._rename_region('R1', '左视图', '左视图')
                self.assertEqual(gallery.patterns['彩条'][0]['region_name'], '左视图')
                self.assertEqual(result['source_regions'][0]['name'], '侧面区域A')
                for category in LABELS:
                    if category != '彩条':
                        gallery._switch_category(category); gallery._absent()
                exported = export_reviewed(gallery.result, gallery.choices, base/'out', patterns=gallery.patterns)
                entries = exported['manifest']['categories']['彩条']['patterns']
                self.assertEqual([e['region_name'] for e in entries], ['左视图', '后视图'])
                svg = Path(exported['output_dir'], entries[0]['files'][0]).read_text(encoding='utf8')
                layer = ET.fromstring(svg).find('.//{http://www.w3.org/2000/svg}g[@id="layer-stripes"]')
                self.assertIsNotNone(layer)
                self.assertEqual(layer.get('{http://www.inkscape.org/namespaces/inkscape}label'), '彩条_左视图')
                self.assertIn('左视图', entries[0]['files'][0])
                restored = ReviewGallery(root, result, base/'out'); restored.withdraw()
                self.addCleanup(restored.destroy)
                self.assertEqual(restored.regions[0]['name'], '左视图')
                self.assertEqual(restored.patterns, gallery.patterns)

    def test_export_splits_legacy_mixed_pattern_and_preserves_unknown_region_name(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            result = fixture(base)
            regions = proposals()
            regions[0].update(bbox=[20, 0, 30, 8], selected_ids=['1'])
            regions[1].update(bbox=[100, 0, 110, 8], selected_ids=['5'])
            result['source_regions'] = regions
            # Candidate direction must not override an explicitly uncertain region.
            result['component_candidates'] = [{'selected_ids': ['1'], 'source_view': '右视图'}]
            choices = {n: None for n in LABELS}; choices['彩条'] = ['1', '5']
            patterns = {n: [] for n in LABELS}
            patterns['彩条'] = [{'selected_ids': ['1', '5'], 'source_view': '混合视图'}]
            exported = export_reviewed(result, choices, base/'out', patterns=patterns)
            entries = exported['manifest']['categories']['彩条']['patterns']
            self.assertEqual([e['source_view'] for e in entries], ['待确认视图', '后视图'])
            self.assertEqual([e['selected_ids'] for e in entries], [['1'], ['5']])
            self.assertIn('侧面区域A', entries[0]['files'][0])
            self.assertTrue(entries[0]['files'][0].startswith('侧面区域A_彩条_01_'))
            self.assertEqual(patterns['彩条'][0]['selected_ids'], ['1', '5'])

    def test_manual_multiregion_selection_splits_without_changing_geometry(self):
        with tempfile.TemporaryDirectory() as temp:
            result = fixture(Path(temp))
            regions = proposals()
            regions[0].update(bbox=[20, 0, 30, 8], selected_ids=['1'])
            regions[1].update(bbox=[100, 0, 110, 8], selected_ids=['5'])
            result['source_regions'] = regions
            root = tk.Tk(); root.withdraw(); self.addCleanup(root.destroy)
            gallery = ReviewGallery(root, result, Path(temp)/'out'); gallery.withdraw()
            self.addCleanup(gallery.destroy)
            gallery._add_pattern('彩条', ['1', '5', '3'])
            self.assertEqual([e['selected_ids'] for e in gallery.patterns['彩条']], [['1'], ['5'], ['3']])
            self.assertNotIn('source_region', gallery.patterns['彩条'][2])


if __name__ == '__main__':
    unittest.main()
