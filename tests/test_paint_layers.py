"""Paint recall and SVG layer contracts; no model/network dependencies."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from components import build_candidates
from core.paint import normalize_categories
from core.svg_layers import SVG, INK, LayerOrderError, layered_svg, _same_render
from geometry import svg_document
from reviewed_exports import export_reviewed
from test_reviewed_exports import fixture


def rectangle(oid, fill, x=0):
    return {'id': oid, 'd': f'M{x} 0 L{x+10} 0 L{x+10} 10 L{x} 10 Z',
            'bbox': [x, 0, x+10, 10], 'fill': fill, 'source_offset': int(oid),
            'details': {'closed': True}, 'stroke': '#123456', 'stroke_width': .05}


class PaintLayersTests(unittest.TestCase):
    def test_fragmented_paint_survives_global_candidate_limit(self):
        # A large base outside the parent vehicle group, roof/mirror inside.
        # Many bigger surfaces previously pushed both fragments out of the atlas.
        def surface(oid, box, fill='#ffffff'):
            obj = rectangle(str(oid), fill)
            obj['bbox'] = box
            return obj

        for color in ('#ffffff', '#9bdac7'):
            with self.subTest(color=color):
                objects = [surface(0, [0, 0, 460, 130], color),
                           surface(1, [50, 118, 232, 130], color),
                           surface(2, [-18, 80, 38, 107], color),
                           surface(3, [10, 10, 14, 18], color),  # small lamp
                           surface(4, [50, 60, 150, 90], '#444444'),  # glass
                           surface(5, [0, 300, 182, 312], color),  # other view
                           surface(6, [260, 122, 294, 128], color)]  # roof housing
                objects += [surface(100+i, [0, 600, 200, 630+i*3], '#123456')
                            for i in range(30)]
                candidates, coverage = build_candidates(objects, [])
                bundle = next(c for c in candidates if c['native_type']=='PaintRegion'
                              and set(c['member_ids']) == {'0', '1', '2', '6'})
                self.assertLessEqual(len(candidates), 120)
                self.assertGreater(coverage['provided_paint_regions'], 0)
                for oid in ('0', '1', '2', '6'):
                    self.assertTrue(any(c['native_type']=='PaintSurface' and
                                        c['member_ids']==[oid] for c in candidates))
                body = {'id': 'body', 'native_type': 'AGroup',
                        'member_ids': ['1', '2', '3', '4', '6']}
                from components import CATEGORIES, resolve_categories
                categories = {n: [] for n in CATEGORIES}
                categories.update(车身=['body'], 车漆=[bundle['id']])
                resolved = resolve_categories(objects, candidates+[body], {'categories': categories})
                self.assertEqual(resolved['车漆'], ['0', '1', '2', '6'])
                self.assertEqual(resolved['车身'], ['3', '4'])

    def test_large_straight_closed_surface_reaches_candidate_pool(self):
        objects = [rectangle('0', '#dedabf')]
        candidates, coverage = build_candidates(objects, [], limit=120)
        self.assertTrue(any(c['native_type'] == 'PaintSurface' and
                            c['member_ids'] == ['0'] for c in candidates))
        self.assertEqual(coverage['provided_paint_surfaces'], 1)
        self.assertFalse(any('category' in c for c in candidates))
        objects[0]['details']['closed'] = False
        candidates, coverage = build_candidates(objects, [], limit=120)
        self.assertEqual(coverage['provided_paint_surfaces'], 0)

    def test_paint_subtracted_from_body_but_not_shared_with_stripes(self):
        objects = [rectangle('0', '#dedabf'), rectangle('1', 'none')]
        categories = normalize_categories(objects, {'车身': ['0', '1'], '车漆': ['0']})
        self.assertEqual(categories['车身'], ['1'])
        with self.assertRaisesRegex(ValueError, '重叠'):
            normalize_categories(objects, {'车漆': ['0'], '彩条': ['0']})

    def test_two_actual_named_layers_preserve_paths_strokes_and_transform(self):
        objects = [rectangle('0', '#dedabf'), rectangle('1', 'none')]
        svg, info = layered_svg(objects, {'车漆': ['0'], '车身': ['1']},
                                layer_names=('车漆', '车身'))
        root = ET.fromstring(svg)
        groups = root.findall(f'.//{{{SVG}}}g[@data-layer]')
        self.assertEqual([g.get('id') for g in groups], ['layer-paint', 'layer-body'])
        self.assertEqual([g.get(f'{{{INK}}}label') for g in groups], ['车漆', '车身'])
        self.assertTrue(all(g.get(f'{{{INK}}}groupmode') == 'layer' for g in groups))
        original = ET.fromstring(svg_document(objects))
        for old in original.findall(f'.//{{{SVG}}}path'):
            new = root.find(f'.//{{{SVG}}}path[@id="{old.get("id")}"]')
            for key, value in old.attrib.items():
                self.assertEqual(new.get(key), value)
        self.assertEqual(groups[0].find(f'{{{SVG}}}path').get('data-paint-editable'), 'fill')
        self.assertTrue(info['exportable'])
        self.assertTrue(_same_render(svg_document(objects), svg))

    def test_gradient_stops_and_source_geometry_are_preserved(self):
        obj = rectangle('0', '#dedabf')
        obj['gradient'] = {'definition': {'kind': 'linear', 'stops': [
            {'offset': 0, 'color': '#eeeeee'}, {'offset': 1, 'color': '#777777'}]},
            'appearance': {'start': [0, 0], 'end': [10, 0], 'matrix': [1, 0, 0, 1, 0, 0]}}
        before = deepcopy(obj)
        svg, _ = layered_svg([obj], {'车漆': ['0']}, layer_names=('车漆', '车身'))
        root = ET.fromstring(svg)
        self.assertEqual([s.get('stop-color') for s in root.findall(f'.//{{{SVG}}}stop')],
                         ['#eeeeee', '#777777'])
        self.assertEqual(root.find(f'.//{{{SVG}}}path').get('fill'), 'url(#fs-gradient-0)')
        self.assertEqual(obj, before)

    def test_conflicting_overlap_is_rejected_and_diagnostic_retains_source_order(self):
        objects = [rectangle('0', '#ff0000'), rectangle('1', '#0000ff')]
        cats = {'车身': ['0'], '车漆': ['1']}
        with self.assertRaises(LayerOrderError):
            layered_svg(objects, cats, layer_names=('车漆', '车身'))
        svg, info = layered_svg(objects, cats, strict=False, layer_names=('车漆', '车身'))
        self.assertFalse(info['exportable'])
        self.assertTrue(_same_render(svg_document(objects), svg))
        self.assertNotIn('id="layer-paint"', svg)

    def test_disjoint_source_order_can_be_safely_regrouped(self):
        objects = [rectangle('0', '#ff0000'), rectangle('1', '#0000ff', 20)]
        _, info = layered_svg(objects, {'车身': ['0'], '车漆': ['1']},
                              layer_names=('车漆', '车身'))
        self.assertTrue(info['exportable'])
        self.assertEqual(info['order_check'], 'render_640_1600')

    def test_source_references_preserve_overlap_and_editable_semantic_paths(self):
        objects = [rectangle('0', '#ff0000'), rectangle('1', '#0000ff')]
        svg, info = layered_svg(objects, {'车身': ['0'], '车漆': ['1']},
                                layer_names=('车漆', '车身'), source_order_fallback=True)
        self.assertEqual(info['rendering'], 'source_order_references')
        self.assertTrue(info['exportable'])
        self.assertTrue(_same_render(svg_document(objects), svg))
        root = ET.fromstring(svg)
        paint = root.find(f'.//{{{SVG}}}g[@id="layer-paint"]/{{{SVG}}}path')
        self.assertEqual(paint.get('data-paint-editable'), 'fill')
        uses = root.findall(f'.//{{{SVG}}}use')
        self.assertEqual([n.get('href') for n in uses], ['#0', '#1'])
        self.assertEqual(len(root.findall(f'.//{{{SVG}}}path')), 2)
        paint.set('fill', '#00ff00')
        recolored = deepcopy(objects)
        recolored[1]['fill'] = '#00ff00'
        self.assertTrue(_same_render(svg_document(recolored), ET.tostring(root, encoding='unicode')))

    def test_final_base_and_individual_exports_have_machine_readable_layers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = fixture(root)
            choices = dict(result['component_categories'], 车漆=['5'])
            published = export_reviewed(result, choices, root/'out')
            folder = Path(published['output_dir'])
            tree = ET.parse(folder/'vehicle-base.svg')
            groups = tree.findall(f'.//{{{SVG}}}g[@data-layer]')
            self.assertEqual([g.get('data-layer') for g in groups], ['paint', 'body'])
            self.assertEqual([p.get('id') for p in groups[0].findall(f'{{{SVG}}}path')], ['5'])
            data = json.loads(tree.find(f'{{{SVG}}}metadata').text)
            self.assertTrue(data['human_reviewed'])
            self.assertEqual(data['source_sha256'], result['source_sha256'])
            self.assertIn('vehicle-base.svg', published['manifest']['artifact_sha256'])
            single = ET.parse(next(folder.glob('车漆_*.svg')))
            self.assertIsNotNone(single.find(f'.//{{{SVG}}}g[@id="layer-paint"]'))

    def test_conflicting_new_base_never_overwrites_previous_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = fixture(root)
            choices = dict(result['component_categories'])
            first = export_reviewed(result, choices, root/'out')
            folder = Path(first['output_dir'])
            before = {p.name: p.read_bytes() for p in folder.iterdir()}
            result['objects'][5] = dict(result['objects'][0], id='5', fill='#ff0000')
            choices['车漆'] = ['5']
            # Even the source-order fallback must pass rendering validation.
            with patch('core.svg_layers._same_render', return_value=False):
                with self.assertRaises(LayerOrderError):
                    export_reviewed(result, choices, root/'out')
            self.assertEqual(before, {p.name: p.read_bytes() for p in folder.iterdir()})
            self.assertFalse(list((root/'out').glob('.review_stage_*')))

    def test_unknown_paint_never_publishes_diagnostic_grey_as_source_colour(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = fixture(root)
            result['objects'][5]['fill'] = None
            choices = dict(result['component_categories'], 车漆=['5'])
            with self.assertRaisesRegex(ValueError, '未知或未支持'):
                export_reviewed(result, choices, root/'out')
            self.assertFalse((root/'out').exists())


if __name__ == '__main__':
    unittest.main()
