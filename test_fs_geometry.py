"""Regression cases for FS endpoint/control ordering and primitive transforms."""
import struct
import unittest

from core.fs_geometry import decode_oval, decode_simple


def record(values):
    return b'\x03' + b''.join(bytes([k]) + struct.pack('<I', len(v) + 5) + v
                              for k, v in values.items())


class GeometryTests(unittest.TestCase):
    def test_cubic_endpoint_precedes_control_handles(self):
        geometry = struct.pack('<2dI6d', 1, 2, 4, 9, 10, 3, 4, 5, 6)
        payload = record({0: record({}), 1: struct.pack('<II', 4, 1), 2: geometry})
        path, points, _, details = decode_simple(payload, 4)
        self.assertEqual(path, 'M1,2 C3,4 5,6 9,10')
        self.assertEqual(points[-1], (9, 10))
        self.assertFalse(details['closed'])

    def test_closed_endpoint_adds_close_command(self):
        geometry = struct.pack('<2dI2dI2d', 1, 2, 2, 5, 6, 2, 1, 2)
        payload = record({0: record({}), 1: struct.pack('<II', 3, 2), 2: geometry})
        self.assertEqual(decode_simple(payload, 4)[0], 'M1,2 L5,6 L1,2 Z')

    def test_ellipse_applies_reflected_sheared_matrix_once(self):
        matrix = (-2, 0, 1, 3, 10, 20)
        payload = record({0: record({1: struct.pack('<6d', *matrix)}),
                          1: record({1: struct.pack('<2d', 8, 4)})})
        path, bounds, _, details = decode_oval(payload, 4)
        # Centre = (4,26), projected radii = (sqrt(68),6).
        self.assertAlmostEqual(bounds[0][0], 4 - 68 ** .5)
        self.assertEqual(bounds[0][1], 20)
        self.assertEqual(bounds[1][1], 32)
        self.assertEqual(path.count('A'), 2)
        self.assertTrue(details['closed'])


if __name__ == '__main__':
    unittest.main()
