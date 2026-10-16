"""Regressions for native brush display flags, independent of sample IDs."""
import struct
import unittest
from unittest.mock import patch

from core.fs_attributes import load_appearances, resolve_appearance
from geometry import svg_document
from core.fs_scene import compose_scene


def record(fields, word):
    return b'\x03' + b''.join(bytes([key]) + (1 + word + len(value)).to_bytes(word, 'little')
                            + value for key, value in fields.items())


class BrushDisplayTests(unittest.TestCase):
    def refs(self):
        return {'classes': {9: 'ASolidBrush'},
                'solid_brushes': {41: {'color_id': 5}},
                'colors': {5: {'hex': '#18293a'}}}

    def appearance(self, wireframe, word):
        common = record({1: struct.pack('<I', 41), 2: struct.pack('<I', 0)}, word)
        body = record({0: common, 1: struct.pack('<I', wireframe), 2: struct.pack('<I', 0)}, word)
        return struct.pack('<II', 130, 9) + len(body).to_bytes(word, 'little') + body + b'\r\x90'

    def test_wireframe_never_covers_existing_fill(self):
        for word in (4, 8):
            with self.subTest(word=word):
                raw = self.appearance(1, word)
                appearance = resolve_appearance(raw, self.refs(), word)[0]
                self.assertTrue(appearance['wireframe_only'])
                with patch('core.fs_attributes.decode_references', return_value=self.refs()):
                    objs, _ = load_appearances(None, [{'appearance_hex': raw.hex()}], word)
                self.assertEqual(objs[0]['fill'], 'none')
                self.assertEqual(objs[0]['stroke'], '#18293a')
                self.assertTrue(objs[0]['stroke_hairline'])

    def test_normal_brush_keeps_original_rgb(self):
        with patch('core.fs_attributes.decode_references', return_value=self.refs()):
            objs, _ = load_appearances(None, [{'appearance_hex': self.appearance(0, 4).hex()}], 4)
        self.assertEqual(objs[0]['fill'], '#18293a')
        self.assertNotIn('stroke', objs[0])

    def test_object_id_can_start_with_sentinel_bytes(self):
        raw = self.appearance(0, 4)
        raw = struct.pack('<I', 0x900d) + raw[4:]
        appearance, = resolve_appearance(raw, self.refs(), 4)
        self.assertEqual(appearance['object_id'], 0x900d)
        self.assertEqual(appearance['color']['hex'], '#18293a')

    def test_svg_draws_outline_with_no_fill(self):
        svg = svg_document([{'id': 'outline', 'd': 'M0 0 H10 V10 Z',
                             'bbox': [0, 0, 10, 10], 'fill': 'none',
                             'stroke': '#18293a', 'stroke_hairline': True}])
        self.assertIn('fill="none"', svg)
        self.assertIn('stroke="#18293a"', svg)


class SceneDisplayTests(unittest.TestCase):
    def path(self, oid, offset, fill):
        return {'object_id': oid, 'id':str(oid), 'source_offset':offset,
                'class_name':'APath', 'fill':fill, 'bbox':[0,0,10,10],
                'd':'M0 0 H10 V10 H0 Z'}

    def test_compound_uses_parent_color_and_one_evenodd_path(self):
        parent = {'object_id':1, 'class_name':'AComposite', 'source_offset':5,
                  'end_offset':50, 'fill':'#abcdef'}
        scene = compose_scene([self.path(2,10,'#ff0000'), self.path(3,20,'#000000')], [parent])
        self.assertEqual(len(scene), 1)
        self.assertEqual(scene[0]['fill'], '#abcdef')
        self.assertEqual(scene[0]['d'].count('M'), 2)
        self.assertIn('fill-rule="evenodd"', svg_document(scene))

    def test_effect_draws_stored_result_and_omits_editing_operands(self):
        effect = {'object_id':1, 'class_name':'ACombineCutOut', 'source_offset':5,
                  'end_offset':50, 'effect_result_span':[30,49]}
        scene = compose_scene([self.path(2,10,'#ff0000'), self.path(3,35,'#0000ff')], [effect])
        self.assertEqual([obj['object_id'] for obj in scene], [3])

    def test_hidden_group_and_hidden_leaf_are_skipped(self):
        group = {'object_id':1, 'class_name':'AGroup', 'source_offset':5,
                 'end_offset':50, 'object_flags':0x08000000}
        hidden = dict(self.path(4,60,'#000000'), object_flags=0x08000000)
        scene = compose_scene([self.path(2,10,'#ff0000'), hidden, self.path(5,70,'#0000ff')], [group])
        self.assertEqual([obj['object_id'] for obj in scene], [5])


if __name__ == '__main__':
    unittest.main()
