"""Layering must retain native cosmetic strokes and source-order protection."""
from copy import deepcopy
import unittest
import xml.etree.ElementTree as ET

from core.svg_layers import layered_svg, _same_render
from geometry import svg_document
from vector_preview import VectorImage


def objects():
    return [
        {'id': 'paint', 'd': 'M0 0H100V30H0Z', 'bbox': [0, 0, 100, 30], 'fill': '#aabbcc'},
        {'id': 'edge', 'd': 'M10 1L90 29', 'bbox': [20, 2, 180, 58],
         'fill': 'none', 'stroke': '#000000', 'stroke_hairline': True,
         'matrix': [2, 0, 0, 2, 0, 0]},
    ]


class LayeredHairlineRenderingTests(unittest.TestCase):
    def test_semantic_layers_match_original_at_multiple_scales(self):
        source = objects()
        before = deepcopy(source)
        svg, info = layered_svg(source, {'车漆': ['paint'], '车身': ['edge']},
                                layer_names=('车漆', '车身'))
        self.assertEqual(info['rendering'], 'grouped_paths')
        self.assertTrue(_same_render(svg_document(source), svg))
        vectors = [VectorImage(svg_document(source)), VectorImage(svg)]
        for scale in (.5, 2, 8):
            with self.subTest(scale=scale):
                tiles = [v.render(scale, (-13, -7), (600, 300))[0] for v in vectors]
                self.assertEqual(tiles[0].tobytes(), tiles[1].tobytes())
        self.assertEqual(source, before)

    def test_references_preserve_transformed_hairline_and_editable_paint(self):
        source = list(reversed(objects()))
        svg, info = layered_svg(source, {'车漆': ['paint'], '车身': ['edge']},
                                layer_names=('车漆', '车身'), source_order_fallback=True)
        self.assertEqual(info['rendering'], 'source_order_references')
        self.assertTrue(_same_render(svg_document(source), svg))
        root = ET.fromstring(svg)
        paint = next(n for n in root.iter() if n.get('id') == 'paint')
        self.assertEqual(paint.get('data-paint-editable'), 'fill')
        paint.set('fill', '#00ff00')
        changed = deepcopy(source)
        changed[1]['fill'] = '#00ff00'
        self.assertTrue(_same_render(svg_document(changed), ET.tostring(root, encoding='unicode')))

    def test_actual_reordering_still_fails_without_source_order_references(self):
        source = list(reversed(objects()))
        from core.svg_layers import LayerOrderError
        with self.assertRaises(LayerOrderError):
            layered_svg(source, {'车漆': ['paint'], '车身': ['edge']},
                        layer_names=('车漆', '车身'))

    def test_general_group_styles_and_reference_cycles_keep_svg_importer(self):
        for content in (
            '<g opacity="0.5"><path id="edge" d="M0 5H10" fill="none" '
            'stroke="#000000" data-fs-hairline="true"/></g>',
            '<defs><g id="loop"><use href="#loop"/></g></defs><use href="#loop"/>'
            '<path id="edge" d="M0 5H10" fill="none" stroke="#000000" data-fs-hairline="true"/>',
        ):
            vector = VectorImage('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" '
                                 'viewBox="0 0 10 10">'+content+'</svg>')
            self.assertIsNone(vector._picture)


if __name__ == '__main__':
    unittest.main()
