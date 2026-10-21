"""Offline coverage of region validation, inheritance, naming and review round trips."""
import copy
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from tests.tk_lifecycle import TkTestCase
import xml.etree.ElementTree as ET
from unittest.mock import patch

from core.source_regions import build_source_regions, object_regions, region_for_ids, split_by_region
from review_gallery import ReviewGallery
from reviewed_exports import LABELS, export_reviewed
from test_reviewed_exports import fixture
from vision import _validate_components, VisionError


def proposals():
    return [{'id': 'R1', 'name': '侧面区域A', 'source_view': '待确认视图', 'anchor_ids': ['G1']},
            {'id': 'R2', 'name': '后视图', 'source_view': '后视图', 'anchor_ids': ['G2']}]


def paint_view_fixture(root):
    """Small rectangles reproducing the four-view boundary drift of 3261129."""
    result = fixture(root)
    boxes = [[57.377, 292.089, 529.304, 428.282], [122.262, 416.603, 304.429, 428.234],
             [60.954, 111.360, 532.881, 247.553], [285.780, 235.725, 467.947, 247.356],
             [570.583, 291.982, 669.735, 429.001], [570.593, 115.356, 669.700, 238.610]]
    for obj, box in zip(result['objects'], boxes):
        x1, y1, x2, y2 = box
        obj.update(bbox=box, d=f'M{x1} {y1}H{x2}V{y2}H{x1}Z')
    result['component_categories'] = {n: [] for n in LABELS}
    result['component_categories']['车漆'] = [o['id'] for o in result['objects']]
    result['source_regions'] = [
        dict(id=f'R{i}', name=name, source_view=name, anchor_ids=[f'G{i}'],
             selected_ids=[f'body-{i}'], bbox=box)
        for i, (name, box) in enumerate([
            ('右视图', [60.905, 97.603, 532.789, 247.404]),
            ('左视图', [57.420, 278.481, 529.304, 428.282]),
            ('前视图', [570.540, 280.462, 669.753, 428.787]),
            ('后视图', [569.368, 99.133, 670.925, 238.546])], 1)]
    return result


class PaintRegionGeometryTests(unittest.TestCase):
    def test_classified_paint_drift_has_bounded_tolerance_and_rejects_ambiguity(self):
        region = dict(id='R1', bbox=[0, 0, 100, 100], selected_ids=['body'])
        objects = [dict(id='paint', bbox=[0, 10, 100.15, 100.15]),
                   dict(id='crossing', bbox=[0, 10, 101, 100]),
                   dict(id='unclassified', bbox=[0, 10, 100.15, 100.15])]
        self.assertEqual(object_regions(objects, [region]), {})
        self.assertEqual(object_regions(objects, [region], paint_ids=['paint', 'crossing']),
                         {'paint': 'R1'})
        self.assertEqual(object_regions(objects, [region, dict(region, id='R2')],
                                        paint_ids=['paint']), {})

    def test_unknown_paint_splits_disconnected_locations_but_keeps_touching_paths(self):
        objects = [dict(id='a', bbox=[0, 0, 10, 10]),
                   dict(id='b', bbox=[20, 0, 30, 10]),
                   dict(id='bridge', bbox=[10, 0, 20, 10]),
                   dict(id='front', bbox=[100, 0, 110, 10]),
                   dict(id='rear', bbox=[100, 50, 110, 60]),
                   dict(id='missing')]
        ids = [o['id'] for o in objects]
        self.assertEqual(split_by_region(ids, {}, objects=objects),
                         [['a', 'b', 'bridge'], ['front'], ['rear'], ['missing']])
        self.assertEqual(split_by_region(ids, {}), [ids])


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

    def test_paint_panel_rounding_drift_stays_with_view_but_crossing_does_not(self):
        region = dict(id='R1', bbox=[100, 200, 440, 336], selected_ids=['body'])
        objects = [dict(id='base', bbox=[110, 215, 440.003, 336.027]),
                   dict(id='outside', bbox=[110, 215, 441, 336]),
                   dict(id='roof', bbox=[120, 330, 130, 335])]
        self.assertEqual(object_regions(objects, [region]), {'base': 'R1', 'roof': 'R1'})
        self.assertNotIn('base', object_regions(objects, [region, dict(region, id='R2')]))

    def test_gallery_and_export_keep_four_paint_views_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            result = paint_view_fixture(Path(temp))
            root = tk.Tk(); root.withdraw(); self.addCleanup(root.destroy)
            gallery = ReviewGallery(root, result, Path(temp)/'out'); gallery.withdraw()
            self.addCleanup(gallery.destroy)
            gallery._switch_category('车漆')
            expected = [['0', '1'], ['2', '3'], ['4'], ['5']]
            self.assertEqual([c['selected_ids'] for c in gallery.filtered], expected)
            self.assertEqual([gallery._region(ids)['source_view'] for ids in expected],
                             ['左视图', '右视图', '前视图', '后视图'])
            gallery._add_pattern('车漆', result['component_categories']['车漆'])
            self.assertEqual([e['selected_ids'] for e in gallery.patterns['车漆']], expected)
            # Legacy saved records mixing views must split at export too.
            choices = {n: None for n in LABELS}
            choices['车漆'] = result['component_categories']['车漆']
            patterns = {n: [] for n in LABELS}
            patterns['车漆'] = [dict(selected_ids=choices['车漆'], source_view='混合视图')]
            exported = export_reviewed(result, choices, Path(temp)/'out', patterns=patterns)
            entries = exported['manifest']['categories']['车漆']['patterns']
            self.assertEqual([e['selected_ids'] for e in entries], expected)
            self.assertEqual([e['source_view'] for e in entries],
                             ['左视图', '右视图', '前视图', '后视图'])

    def test_unknown_paint_locations_stay_separate_in_gallery_and_export(self):
        with tempfile.TemporaryDirectory() as temp:
            result = fixture(Path(temp))
            result['component_categories'] = {n: [] for n in LABELS}
            result['component_categories']['车漆'] = ['0', '3', '5']
            root = tk.Tk(); root.withdraw(); self.addCleanup(root.destroy)
            gallery = ReviewGallery(root, result, Path(temp)/'out'); gallery.withdraw()
            self.addCleanup(gallery.destroy)
            gallery._switch_category('车漆')
            self.assertEqual([c['selected_ids'] for c in gallery.filtered], [['0'], ['3'], ['5']])
            gallery._add_pattern('车漆', ['0', '3', '5'])
            self.assertEqual([e['selected_ids'] for e in gallery.patterns['车漆']], [['0'], ['3'], ['5']])
            choices = {n: None for n in LABELS}; choices['车漆'] = ['0', '3', '5']
            patterns = {n: [] for n in LABELS}
            patterns['车漆'] = [dict(selected_ids=choices['车漆'], source_view='混合视图')]
            exported = export_reviewed(result, choices, Path(temp)/'out', patterns=patterns)
            entries = exported['manifest']['categories']['车漆']['patterns']
            self.assertEqual([e['selected_ids'] for e in entries], [['0'], ['3'], ['5']])
            self.assertEqual([e['source_view'] for e in entries], ['待确认视图']*3)

    def test_classified_stripes_bleeding_outside_anchor_keep_separate_regions(self):
        regions = [dict(id='R1', bbox=[0, 100, 100, 140], selected_ids=['left']),
                   dict(id='R2', bbox=[0, 0, 100, 40], selected_ids=['right'])]
        objects = [dict(id='left-stripe', bbox=[5, 95, 100.1, 130]),
                   dict(id='right-stripe', bbox=[-.1, -5, 95, 30]),
                   dict(id='crossing', bbox=[5, 25, 95, 115]),
                   dict(id='unclassified-panel', bbox=[5, 95, 100.1, 130])]
        self.assertEqual(object_regions(objects, regions), {})
        eligible = ['left-stripe', 'right-stripe', 'crossing']
        self.assertEqual(object_regions(objects, regions, partial_ids=eligible),
                         {'left-stripe': 'R1', 'right-stripe': 'R2'})
        overlapping = regions+[dict(regions[0], id='R3')]
        self.assertNotIn('left-stripe', object_regions(objects, overlapping, partial_ids=eligible))

    def test_gallery_and_export_split_overhanging_multicolour_stripes(self):
        with tempfile.TemporaryDirectory() as temp:
            result = fixture(Path(temp))
            # Source rectangles, including geometry, actually extend past anchors.
            for index, obj in enumerate(result['objects']):
                x = index % 3*15
                y = 100 if index < 3 else 0
                obj.update(bbox=[x, y-1, x+10, y+7],
                           d=f'M{x} {y-1}H{x+10}V{y+7}H{x}Z')
            result['component_categories'] = {n: [] for n in LABELS}
            result['component_categories']['彩条'] = [o['id'] for o in result['objects']]
            result['source_regions'] = [
                dict(id='R1', name='左视图', source_view='左视图', anchor_ids=['G1'],
                     bbox=[0, 100, 50, 120], selected_ids=['body-left']),
                dict(id='R2', name='右视图', source_view='右视图', anchor_ids=['G2'],
                     bbox=[0, 0, 50, 20], selected_ids=['body-right'])]
            root = tk.Tk(); root.withdraw(); self.addCleanup(root.destroy)
            gallery = ReviewGallery(root, result, Path(temp)/'out'); gallery.withdraw()
            self.addCleanup(gallery.destroy)
            gallery._switch_category('彩条')
            self.assertEqual([c['selected_ids'] for c in gallery.filtered],
                             [['0', '1', '2'], ['3', '4', '5']])
            # Legacy mixed entries must also split at export without UI help.
            choices = {n: None for n in LABELS}; choices['彩条'] = list(result['component_categories']['彩条'])
            patterns = {n: [] for n in LABELS}
            patterns['彩条'] = [dict(selected_ids=choices['彩条'], source_view='混合视图')]
            exported = export_reviewed(result, choices, Path(temp)/'out', patterns=patterns)
            entries = exported['manifest']['categories']['彩条']['patterns']
            self.assertEqual([e['selected_ids'] for e in entries], [['0', '1', '2'], ['3', '4', '5']])
            self.assertTrue(entries[0]['files'][0].startswith('左视图_彩条_01_'))
            self.assertTrue(entries[1]['files'][0].startswith('右视图_彩条_02_'))

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
