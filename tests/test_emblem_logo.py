"""Complete door badges and reusable embedded logos, without model calls."""
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from tests.tk_lifecycle import TkTestCase
import xml.etree.ElementTree as ET

from components import build_candidates, resolve_categories, review_candidates
from core.paint import normalize_categories
from core.svg_layers import SVG, layered_svg, _same_render
from geometry import svg_document
from review_gallery import ReviewGallery
from reviewed_exports import export_reviewed
from test_components import decision
from test_reviewed_exports import fixture
from vision import _validate_components, VisionError


def badge_scene():
    boxes = [[0, 5, 2, 9], [8, 5, 10, 9], [4, 5, 5, 7], [5, 5, 6, 7], [3, 0, 7, 2]]
    objects = [{'id': str(i), 'source_offset': offset, 'bbox': box,
                'd': f'M{box[0]} {box[1]}H{box[2]}V{box[3]}H{box[0]}Z', 'fill': 'none'}
               for i, (offset, box) in enumerate(zip([20, 30, 50, 60, 80], boxes))]
    containers = [
        {'class_name': 'AGroup', 'source_offset': 0, 'end_offset': 90},
        {'class_name': 'AArcText', 'source_offset': 10, 'end_offset': 40},
        {'class_name': 'AGroup', 'source_offset': 40, 'end_offset': 70}]
    # Bigger unrelated native groups previously displaced the tiny badge logo.
    for i in range(60):
        offset = 1000+i*100
        containers.append({'class_name': 'AGroup', 'source_offset': offset, 'end_offset': offset+90})
        for j in range(2):
            objects.append({'id': f'other-{i}-{j}', 'source_offset': offset+10+j*20,
                            'bbox': [100, 100+i, 200, 150+i], 'd': 'M100 100H200V150Z', 'fill': 'none'})
    return objects, containers


class EmblemLogoTests(TkTestCase):
    def test_small_logo_and_complete_badge_survive_caps_and_keep_logo_with_arc(self):
        objects, containers = badge_scene()
        candidates, coverage = build_candidates(objects, containers)
        full = next(c for c in candidates if c['native_type']=='DoorEmblem')
        logo = next(c for c in candidates if c['native_type']=='EmblemLogo')
        self.assertEqual(set(full['member_ids']), {'0', '1', '2', '3'})
        self.assertEqual(set(logo['member_ids']), {'2', '3'})
        self.assertNotIn('4', full['member_ids'])  # technical seat-count text
        self.assertLessEqual(len(candidates), 120)
        self.assertEqual(coverage['provided_emblem_candidates'], 2)
        picked = decision(门徽=[full['id']], logo=[logo['id']])
        _validate_components(picked, {c['id'] for c in candidates})
        result = resolve_categories(objects, candidates, picked)
        self.assertEqual(result['门徽'], ['0', '1', '2', '3'])
        self.assertEqual(result['logo'], ['2', '3'])
        broad = next(c for c in candidates if set(c['member_ids'])=={'0', '1', '2', '3', '4'})
        resolved_parent = resolve_categories(objects, candidates,
                                            decision(门徽=[broad['id']], logo=[logo['id']]))
        self.assertEqual(resolved_parent['门徽'], ['0', '1', '2', '3'])
        review = review_candidates(candidates, picked)
        self.assertIn('logo', next(c for c in review if c['id']==logo['id'])['category_hints'])

    def test_only_logo_emblem_overlap_allowed_in_model_and_source_protocols(self):
        _validate_components(decision(logo=['same'], 门徽=['same']), {'same'})
        for other in ('彩条', '车漆', '文字', '车身'):
            with self.subTest(other=other), self.assertRaises(VisionError):
                _validate_components(decision(**{'logo': ['same'], other: ['same']}), {'same'})
        objects = [{'id': 'a'}, {'id': 'b'}]
        resolved = normalize_categories(objects, {'车身': ['a', 'b'], 'logo': ['a'], '门徽': ['a']})
        self.assertEqual(resolved['车身'], ['b'])
        with self.assertRaises(ValueError):
            normalize_categories(objects, {'logo': ['a'], '门徽': ['a'], '彩条': ['a']})

    def test_merged_preview_renders_shared_path_once(self):
        obj = {'id': 'shared', 'd': 'M0 0H10V10H0Z', 'bbox': [0, 0, 10, 10], 'fill': '#167ce2'}
        svg, info = layered_svg([obj], {'logo': ['shared'], '门徽': ['shared']})
        self.assertTrue(_same_render(svg_document([obj]), svg))
        root = ET.fromstring(svg)
        self.assertEqual(len(root.findall(f'.//{{{SVG}}}path')), 1)
        self.assertEqual(info['shared_logo_emblem_ids'], ['shared'])

    def test_real_review_and_export_preserve_both_categories(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            result = fixture(path)
            result['component_categories'].update(门徽=['3', '4'], logo=['3'])
            root = tk.Tk()
            root.withdraw()
            try:
                gallery = ReviewGallery(root, result, path/'out')
                gallery.withdraw()
                try:
                    for category in gallery.category_buttons:
                        gallery._switch_category(category)
                        if category == '车漆':
                            gallery._absent()
                        else:
                            gallery._confirm()
                    exported = export_reviewed(result, gallery.choices, path/'out', patterns=gallery.patterns)
                    for label, expected in [('logo', ['3']), ('徽章', ['3', '4'])]:
                        record = exported['manifest']['categories'][label]
                        self.assertEqual(record['selected_ids'], expected)
                        svg = ET.parse(Path(exported['output_dir'])/record['files'][0])
                        self.assertEqual([n.get('id') for n in svg.findall(f'.//{{{SVG}}}path')], expected)
                finally:
                    gallery.destroy()
            finally:
                root.destroy()


if __name__ == '__main__':
    unittest.main()
