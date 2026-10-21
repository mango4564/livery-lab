"""CMYK references with a zero RGB cache must keep their source colors."""
import io
import struct
import unittest
from unittest.mock import patch

from core.fs_attributes import cmyk_display_color, decode_references, load_appearances, resolve_pen
from geometry import svg_document
from test_fs_display import record


def reference_stream(records):
    raw = struct.pack('<H', len(records))
    for oid, cls, fields in records:
        body = record(fields, 4)
        raw += struct.pack('<III', oid, cls, len(body)) + body + b'\r\x90'
    return record({2: raw}, 4)


class CmykColorsTests(unittest.TestCase):
    def test_source_channels_cover_primaries_white_black_and_mixed_ink(self):
        for raw, expected in [(b'\0\xff\xff\0', '#ff0000'),
                              (b'\0\x84\xff\0', '#ff7b00'),
                              (b'\xff\0\0\0', '#00ffff'),
                              (b'\0\0\0\0', '#ffffff'),
                              (b'\0\0\0\xff', '#000000'),
                              (bytes([51, 102, 153, 85]), '#886644')]:
            with self.subTest(raw=raw):
                self.assertEqual(cmyk_display_color({1: raw})['hex'], expected)
        for raw in (b'', b'\0' * 3, b'\0' * 5):
            with self.assertRaises(ValueError):
                cmyk_display_color({1: raw})

    def test_reference_fill_pen_and_svg_ignore_zero_rgb_cache(self):
        common = record({0: record({2: b'\0'}, 4),
                         2: record({0: b'\0' * 6}, 4)}, 4)
        streams = {
            'AColorItem': [(71, 2, {0: common, 1: b'\0\xff\xff\0'}),
                           (72, 1, {10: common, 11: b'\x12\x34\x56'}),
                           (73, 2, {0: common, 1: b'\0' * 3})],
            'ASolidBrushItem': [(81, 5, {1: struct.pack('<I', 71)})],
            'APenItem': [(91, 4, {1: struct.pack('<I', 71), 2: struct.pack('<d', 1.)})],
            'ALayerItem': [],
        }

        class Ole:
            def openstream(self, path):
                name = path.split('/')[-1].removesuffix(' Data')
                return io.BytesIO(reference_stream(streams[name]))

            def exists(self, path):
                return False

        classes = {1: 'ARGBColorItem', 2: 'ACMYKColorItem', 9: 'ASolidBrush', 11: 'APen'}
        with patch('core.fs_attributes.class_names', return_value=classes):
            refs = decode_references(Ole())
        self.assertEqual(refs['colors'][71]['hex'], '#ff0000')
        self.assertEqual(refs['colors'][72]['hex'], '#123456')
        self.assertIsNone(refs['colors'][73]['hex'])
        for word in (4, 8):
            with self.subTest(word=word):
                common_brush = record({1: struct.pack('<I', 81), 2: b'\0' * 4}, word)
                brush = record({0: common_brush, 1: b'\0' * 4}, word)
                appearance = struct.pack('<II', 101, 9) + len(brush).to_bytes(word, 'little') + brush
                pen = record({11: struct.pack('<I', 91)}, word)
                pen_raw = struct.pack('<II', 102, 11) + len(pen).to_bytes(word, 'little') + pen
                self.assertEqual(resolve_pen(pen_raw, refs, word)['stroke'], '#ff0000')
                obj = {'id': 'red', 'd': 'M0 0 H10 V10 Z', 'bbox': [0, 0, 10, 10],
                       'appearance_hex': appearance.hex()}
                with patch('core.fs_attributes.decode_references', return_value=refs):
                    objects, _ = load_appearances(None, [obj], word)
                self.assertEqual(objects[0]['fill'], '#ff0000')
                self.assertEqual(objects[0]['fill_status'], 'source_cmyk_approximation')
                self.assertIn('fill="#ff0000"', svg_document(objects))


if __name__ == '__main__':
    unittest.main()
