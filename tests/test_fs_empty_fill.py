"""A native null brush is transparent, independently of page/background color."""
import unittest
from unittest.mock import patch

from core.fs_attributes import load_appearances
from core.fs_scene import compose_scene
from geometry import svg_document
from test_fs_display import record
from vector_preview import VectorImage
import struct


class EmptyFillTests(unittest.TestCase):
    def refs(self):
        return {'classes': {11: 'APen'}, 'pens': {7: {'color_id': 9, 'width_candidate': 1.}},
                'colors': {9: {'hex': '#125678'}}, 'solid_brushes': {}}

    def test_explicit_null_brush_keeps_independent_pen_for_both_word_widths(self):
        for word in (4, 8):
            with self.subTest(word=word):
                pen = record({11: struct.pack('<I', 7)}, word)
                pen = struct.pack('<II', 100, 11) + len(pen).to_bytes(word, 'little') + pen
                with patch('core.fs_attributes.decode_references', return_value=self.refs()):
                    objects, _ = load_appearances(None, [{'appearance_hex': '00000000',
                                                         'pen_hex': pen.hex()}], word)
                self.assertEqual(objects[0]['fill'], 'none')
                self.assertEqual(objects[0]['fill_status'], 'source_no_fill')
                self.assertEqual(objects[0]['stroke'], '#125678')

    def test_missing_and_unsupported_appearances_are_still_unknown(self):
        body = record({}, 4)
        unsupported = struct.pack('<III', 100, 99, len(body)) + body
        with patch('core.fs_attributes.decode_references', return_value=self.refs()):
            objects, _ = load_appearances(None, [{}, {'appearance_hex': unsupported.hex()}], 4)
        self.assertEqual([o['fill'] for o in objects], [None, None])
        self.assertEqual(objects[1]['fill_status'], 'unsupported_appearance')

    def test_null_brush_does_not_cover_existing_colors_or_transparency(self):
        path = {'id': 'null', 'd': 'M0 0 H10 V10 H0 Z', 'bbox': [0, 0, 10, 10],
                'appearance_hex': '00000000'}
        with patch('core.fs_attributes.decode_references', return_value=self.refs()):
            objects, _ = load_appearances(None, [path], 4)
        for underlay, expected in [(None, None), ('#ff0000', (255, 0, 0)),
                                   ('#ffffff', (255, 255, 255))]:
            with self.subTest(underlay=underlay):
                layers = ([dict(path, id='base', fill=underlay)] if underlay else []) + objects
                svg = svg_document(layers, width=100, margin=0)
                self.assertNotIn('#8b98a8', svg)
                image, _, _ = VectorImage(svg).render(1, (0, 0), (100, 100))
                pixel = image.convert('RGBA').getpixel((50, 50))
                if expected is None:
                    self.assertEqual(pixel[3], 0)
                else:
                    self.assertEqual(pixel, (*expected, 255))

    def test_compound_preserves_explicit_null_brush_instead_of_child_fill(self):
        with patch('core.fs_attributes.decode_references', return_value=self.refs()):
            parents, _ = load_appearances(None, [{'object_id': 1, 'class_name': 'AComposite',
                'appearance_hex': '00000000', 'source_offset': 1, 'end_offset': 30}], 4)
        child = {'object_id': 2, 'source_offset': 10, 'd': 'M0 0 H10 V10 Z',
                 'bbox': [0, 0, 10, 10], 'fill': '#ff0000'}
        scene = compose_scene([child], parents)
        self.assertEqual(scene[0]['fill'], 'none')


if __name__ == '__main__':
    unittest.main()
