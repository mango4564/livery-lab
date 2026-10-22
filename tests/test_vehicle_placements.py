"""Offline export contracts for placement metadata and coordinate mapping."""
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from PIL import Image

from core.placements import PNG_KEY, attach_placements
from core.svg_layers import SVG, layered_svg, _same_render
from geometry import rasterize
from reviewed_exports import LABELS, export_reviewed
from test_reviewed_exports import fixture


class VehiclePlacementsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.result = fixture(self.root)
        self.choices = dict(self.result['component_categories'], 车漆=['5'])
        self.patterns = {name: ([{'selected_ids': ids, 'source_view': '左视图'}] if ids else [])
                         for name, ids in self.choices.items()}
        self.result['source_regions'] = [
            {'id': 'R1', 'name': '左侧', 'source_view': '左视图',
             'bbox': [-5, -5, 115, 15], 'selected_ids': [str(i) for i in range(6)]}]

    def publish(self, patterns=True):
        result = export_reviewed(self.result, self.choices, self.root/'out',
                                 patterns=self.patterns if patterns else None)
        folder = Path(result['output_dir'])
        data = json.loads((folder/'整车底图'/'vehicle-base.placements.json').read_text(encoding='utf8'))
        return result, folder, data

    def test_three_carriers_match_and_manifest_hashes_cover_all_files(self):
        published, folder, data = self.publish()
        embedded = json.loads(ET.parse(folder/'整车底图'/'vehicle-base.svg').find(f'{{{SVG}}}metadata').text)
        with Image.open(folder/'整车底图'/'vehicle-base.png') as image:
            self.assertEqual(json.loads(image.info[PNG_KEY]), data)
            self.assertEqual(list(image.size), data['canvas']['png_size'])
            self.assertEqual(image.mode, 'RGBA')
        self.assertEqual(embedded['placements'], data)
        self.assertEqual(len(data['slots']), 4)
        self.assertTrue(all(s['placement_status'] == 'suggested' for s in data['slots']))
        self.assertTrue(all(len(s['files']) == 2 for s in data['slots']))
        self.assertTrue(all((folder/f).exists() for s in data['slots'] for f in s['files']))
        self.assertIn('整车底图/vehicle-base.placements.json', published['manifest']['artifact_sha256'])
        self.assertEqual(published['manifest']['placements']['template_id'], data['template_id'])
        import hashlib
        for filename, digest in published['manifest']['artifact_sha256'].items():
            self.assertEqual(hashlib.sha256((folder/filename).read_bytes()).hexdigest(), digest)

    def test_mapping_handles_negative_source_transforms_flip_and_tall_png_cap(self):
        # Two colored rectangles with a transformed historical path at the top.
        objects = [
            {'id': 'paint', 'd': 'M0 0 L10 0 L10 101 L0 101 Z',
             'bbox': [-20, -50, -10, 51], 'matrix': [1, 0, 0, 1, -20, -50], 'fill': '#ffffff'},
            {'id': 'logo', 'd': 'M0 0 L2 0 L2 4 L0 4 Z',
             'bbox': [-18, 40, -16, 44], 'matrix': [1, 0, 0, 1, -18, 40], 'fill': '#ff0000'}]
        svg, _ = layered_svg(objects[:1], {'车漆': ['paint']}, bounds=[-20, -50, -10, 51],
                             width=2400, layer_names=('车漆', '车身'))
        png = self.root/'base.png'
        rasterize(svg, png, max_width=2400)
        with Image.open(png) as image:
            before = image.tobytes()
        patterns = {name: [] for name in LABELS}
        patterns['logo'] = [{'selected_ids': ['logo'], 'source_view': '前视图'}]
        changed, data = attach_placements(svg, png, objects, patterns, [], 'digest',
                                          individual_patterns=True)
        self.assertTrue(_same_render(svg, changed))
        with Image.open(png) as image:
            self.assertEqual(image.tobytes(), before)
        self.assertEqual(data['canvas']['png_size'][1], 4000)
        slot = data['slots'][0]
        self.assertEqual(slot['bbox_source'], [-18, 40, 2, 4])
        # Independently render a red rectangle in the same viewBox and compare
        # its visible pixel bounds against the mapped historical bounds.
        overlay, _ = layered_svg(objects[1:], {'logo': ['logo']}, bounds=[-20, -50, -10, 51],
                                 width=2400, layer_names=('logo',))
        rasterize(overlay, self.root/'overlay.png', max_width=2400)
        with Image.open(self.root/'overlay.png') as image:
            actual = image.getchannel('A').getbbox()
        x, y, w, h = slot['bbox_png']
        for expected, observed in zip([x, y, x+w, y+h], actual):
            self.assertAlmostEqual(expected, observed, delta=1.1)
        self.assertLess(slot['bbox_svg'][1], -30)  # top, after source Y flip

    def test_per_pattern_regions_remain_separate_and_shared_logo_is_related(self):
        self.choices['logo'] = ['3', '5']
        self.choices['车漆'] = ['0']
        self.choices['车身'] = None
        self.choices['门徽'] = ['4', '3']
        self.result['source_regions'] = [
            {'id': 'R1', 'name': '左侧', 'source_view': '左视图',
             'bbox': [-5, -5, 95, 15], 'selected_ids': ['0', '1', '2', '3', '4']},
            {'id': 'R2', 'name': '右侧', 'source_view': '右视图',
             'bbox': [95, -5, 115, 15], 'selected_ids': ['5']}]
        self.patterns = {name: ([{'selected_ids': ids, 'source_view': '混合视图'}] if ids else [])
                         for name, ids in self.choices.items()}
        _, _, data = self.publish()
        logos = [s for s in data['slots'] if s['category'] == 'logo']
        emblem = next(s for s in data['slots'] if s['category'] == 'emblem')
        self.assertEqual([s['source_view'] for s in logos], ['左视图', '右视图'])
        self.assertEqual(logos[0]['related_slot_ids'], [emblem['id']])
        self.assertEqual(logos[1]['related_slot_ids'], [])
        self.assertIsNone(logos[0]['rotation_degrees'])

    def test_legacy_or_unresolved_memberships_require_confirmation(self):
        _, _, data = self.publish(patterns=False)
        self.assertTrue(all('legacy_category_group' in s['confirmation_reasons'] for s in data['slots']))
        self.result['source_regions'] = []
        _, _, data = self.publish()
        self.assertTrue(all(s['placement_status'] == 'requires_confirmation' for s in data['slots']))

    def test_two_logos_in_same_region_remain_independent_slots(self):
        self.choices.update(logo=['3', '5'], 车漆=['0'], 车身=None)
        self.patterns = {name: ([{'selected_ids': ids, 'source_view': '左视图'}] if ids else [])
                         for name, ids in self.choices.items()}
        self.patterns['logo'] = [{'selected_ids': [oid], 'source_view': '左视图'} for oid in ('3', '5')]
        _, _, data = self.publish()
        logos = [s for s in data['slots'] if s['category'] == 'logo']
        self.assertEqual(len(logos), 2)
        self.assertNotEqual(logos[0]['id'], logos[1]['id'])
        self.assertNotEqual(logos[0]['bbox_svg'], logos[1]['bbox_svg'])
        self.assertTrue(all(s['source_region'] == 'R1' for s in logos))

    def test_duplicate_patterns_after_logo_exclusion_are_rejected(self):
        self.choices['文字'] = ['2', '3']
        self.patterns['文字'] = [{'selected_ids': ['2', '3'], 'source_view': '左视图'},
                                {'selected_ids': ['2'], 'source_view': '左视图'}]
        with self.assertRaisesRegex(ValueError, '位置重复'):
            self.publish()
        self.assertFalse((self.root/'out'/Path(self.result['source']).stem).exists())

    def test_ids_stable_but_template_changes_with_reviewed_base(self):
        _, _, first = self.publish()
        _, _, second = self.publish()
        self.assertEqual(first, second)
        self.choices['车身'] = None
        self.patterns['车身'] = []
        _, _, third = self.publish()
        self.assertNotEqual(first['template_id'], third['template_id'])
        self.assertEqual([s['id'] for s in first['slots']], [s['id'] for s in third['slots']])

    def test_no_paint_keeps_existing_no_base_contract(self):
        self.choices['车漆'] = None
        self.patterns['车漆'] = []
        published = export_reviewed(self.result, self.choices, self.root/'out', patterns=self.patterns)
        self.assertEqual(list(Path(published['output_dir']).rglob('vehicle-base*')), [])

    def test_empty_graphics_produce_empty_slots(self):
        for name in ('彩条', '文字', 'logo', '门徽'):
            self.choices[name] = None
            self.patterns[name] = []
        _, _, data = self.publish()
        self.assertEqual(data['slots'], [])

    def test_bad_bounds_and_embedding_failure_keep_previous_exports(self):
        _, folder, _ = self.publish()
        before = {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        self.result['objects'][1]['bbox'] = [0, 0, float('nan'), 8]
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(before, {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()})
        self.result['objects'][1]['bbox'] = [20, 0, 30, 8]
        with patch('core.placements.PngImagePlugin.PngInfo.add_itxt', side_effect=OSError('write failed')):
            with self.assertRaises(OSError):
                self.publish()
        self.assertEqual(before, {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()})
        self.assertEqual(list((self.root/'out').glob('.review_stage_*')), [])


if __name__ == '__main__':
    unittest.main()
