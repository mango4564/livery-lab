"""LAB uses fields 10/11, and field 11 contains signed chroma, not RGB."""
import io
import unittest
from unittest.mock import patch

from core.fs_attributes import decode_references, lab_display_color
from tests.test_fs_cmyk_colors import reference_stream
from test_fs_display import record


class LabColorTests(unittest.TestCase):
    def test_unsigned_lightness_and_signed_chroma(self):
        black = lab_display_color({11: b'\0\0\0'})['rgb']
        grey = lab_display_color({11: b'\x80\0\0'})['rgb']
        white = lab_display_color({11: b'\xff\0\0'})['rgb']
        self.assertEqual(black, [0, 0, 0])
        self.assertEqual(max(grey), min(grey))
        self.assertTrue(110 < grey[0] < 130)
        self.assertTrue(all(v >= 254 for v in white))
        # a=-4, b=88 is yellow, not the magenta obtained by treating a as +124.
        color = lab_display_color({11: bytes.fromhex('e7fc58')})
        self.assertGreater(color['rgb'][0], 240)
        self.assertGreater(color['rgb'][1], 200)
        self.assertLess(color['rgb'][2], 40)
        self.assertNotEqual(color['hex'], '#e7fc58')

    def test_lab_reference_without_field_zero_loads_with_document_local_class_ids(self):
        common = record({0: record({2: b'\0'}, 4),
                         2: record({0: b'\0' * 6}, 4)}, 4)
        for lab_id, rgb_id in ((4, 1), (1, 9)):
            with self.subTest(lab_id=lab_id):
                colors = reference_stream([(38, lab_id, {10: common, 11: bytes.fromhex('e7fc58')}),
                                           (39, rgb_id, {10: common, 11: bytes.fromhex('e7fc58')}),
                                           (40, lab_id, {10: common, 11: b'\0' * 2})])
                class Ole:
                    def openstream(self, path):
                        return io.BytesIO(colors if path.endswith('/AColorItem Data')
                                          else reference_stream([]))

                    def exists(self, path):
                        return False

                with patch('core.fs_attributes.class_names', return_value={lab_id: 'ALABColorItem',
                                                                          rgb_id: 'ARGBColorItem'}):
                    refs = decode_references(Ole())
                self.assertEqual(refs['colors'][38]['status'], 'source_lab_approximation')
                self.assertGreater(refs['colors'][38]['rgb'][1], 200)
                self.assertEqual(refs['colors'][39]['hex'], '#e7fc58')
                self.assertIsNone(refs['colors'][40]['hex'])
                self.assertEqual(refs['colors'][40]['status'], 'unsupported_lab')

    def test_bad_channel_sizes_are_rejected(self):
        for raw in (b'', b'\0' * 2, b'\0' * 4):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                lab_display_color({11: raw})


if __name__ == '__main__':
    unittest.main()
