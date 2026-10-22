"""Rectangle primitives must survive FS extraction and source-space export."""
import struct
import unittest
from unittest.mock import patch

from core.fs_geometry import decode_rectangle, extract_geometry


def payload(word=4, matrix=(-1, 0, 0, 1, 145, 190), corner=0, size=(69, 34), style=2):
    def record(values):
        return b'\x05' + b''.join(bytes([k]) + (len(v)+word+1).to_bytes(word, 'little') + v
                                  for k, v in values.items())
    base = record({0: record({1: bytes(4)}), 1: struct.pack('<6d', *matrix)})
    return record({0: base, 1: record({1: struct.pack('<2d', *size),
                                      2: struct.pack('<dB', corner, style), 3: bytes(4)})})


class RectangleTests(unittest.TestCase):
    def test_mirrored_rectangle_is_closed_and_transformed_once(self):
        for word in (4, 8):
            path, points, _, details = decode_rectangle(payload(word), word)
            self.assertEqual(path, 'M145,190 L76,190 L76,224 L145,224 Z')
            self.assertEqual(points, [(145, 190), (76, 190), (76, 224), (145, 224)])
            self.assertTrue(details['closed'])

    def test_shear_transforms_all_four_corners(self):
        _, points, _, _ = decode_rectangle(payload(matrix=(2, 1, 3, 4, 5, 6), size=(10, 20)), 4)
        self.assertEqual(points, [(5, 6), (25, 16), (85, 96), (65, 86)])

    def test_sharp_style_ignores_retained_corner_amount(self):
        # FS keeps an old corner percentage even after switching to sharp.
        # Native ARectangle::convertToPath style 1 never reads that amount.
        for word in (4, 8):
            for amount in (11.223778466467024, 353.0211366027331):
                with self.subTest(word=word, amount=amount):
                    actual = decode_rectangle(payload(word, corner=amount, style=1), word)
                    expected = decode_rectangle(payload(word, corner=0, style=1), word)
                    self.assertEqual(actual[:3], expected[:3])
                    self.assertEqual(actual[3]['corner_style'], 1)
                    self.assertEqual(actual[3]['stored_corner_amount'], amount)

    def test_unsupported_corners_and_invalid_geometry_are_rejected(self):
        for kwargs in ({'corner': .5}, {'size': (0, 10)}, {'size': (float('nan'), 10)},
                       {'matrix': (0, 0, 0, 1, 0, 0)}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                decode_rectangle(payload(**kwargs), 4)

    def test_object_scanner_includes_rectangles(self):
        body = payload(corner=353.0211366027331, style=1)
        raw = struct.pack('<III', 13315, 1, len(body)) + body + b'\r\x90'
        with patch('core.fs_geometry.olefile.OleFileIO') as ole, \
                patch('core.fs_geometry.class_names', return_value=['NULL', 'ARectangle']), \
                patch('core.fs_geometry.unpack_objects', return_value=(raw, {'tlv_word_bytes': 4})):
            doc = extract_geometry('example.fs')
        self.assertEqual(len(doc['objects']), 1)
        obj = doc['objects'][0]
        self.assertEqual(obj['object_id'], 13315)
        self.assertEqual(obj['bbox'], [76, 190, 145, 224])


if __name__ == '__main__':
    unittest.main()
