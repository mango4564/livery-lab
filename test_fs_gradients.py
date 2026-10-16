import struct
import unittest
from xml.etree import ElementTree as ET

from core.fs_gradients import decode_gradient_record, display_color, gradient_appearances, gradient_svg, spot_display_color


def record(fields, word=4):
    return b'\1' + b''.join(bytes([key]) + (len(value) + word + 1).to_bytes(word, 'little') + value
                           for key, value in fields.items())


class GradientTests(unittest.TestCase):
    def test_nested_stop_records_use_own_offsets_and_rgb(self):
        entries = []
        for oid, offset, rgb in [(81, 0., '00ff00ff00ff'), (83, .6375142793797337, '009700970099'),
                                 (85, 1., '006b006c0072')]:
            body = record({1: record({0: bytes.fromhex(rgb)}), 2: struct.pack('<d', offset)})
            entries.append(struct.pack('<III', oid, 7, len(body)) + body + b'\r\x90')
        stops = struct.pack('<H', 3) + b''.join(entries)
        g = record({5: struct.pack('<I', 2), 7: struct.pack('<I', 256),
                    9: record({5: record({2: stops})})})
        source = {'class_id': 6, 'fields': {1: g, 2: struct.pack('<2d', .5, .8),
                                          3: struct.pack('<2d', .5, 2.)}}
        decoded = decode_gradient_record(source, {7: 'AGradientColor'})
        self.assertEqual(decoded['kind'], 'radial')
        self.assertEqual([s['color'] for s in decoded['stops']], ['#ffffff', '#979799', '#6b6c72'])
        self.assertEqual(decoded['stops'][1]['offset'], .6375142793797337)

    def test_display_color_and_tint_match_native_integer_formula(self):
        color = record({0: bytes.fromhex('00b000a800eb')})
        self.assertEqual(display_color(color)['hex'], '#b0a8eb')
        common = record({2: color})
        self.assertEqual(spot_display_color({'fields': {0: common, 1: b'\x64'}})['hex'], '#b0a8eb')
        self.assertEqual(spot_display_color({'fields': {0: common, 1: b'\x32'}})['hex'], '#d8d4f5')
        self.assertEqual(spot_display_color({'fields': {0: common, 1: b'\0'}})['hex'], '#ffffff')

    def test_per_object_frame_and_endpoints_override_shared_reference(self):
        base = record({1: struct.pack('<I', 176), 2: struct.pack('<I', 0)})
        frame = (8., 0., 0., 3., 625., 404.)
        common = record({0: base, 1: struct.pack('<6d', *frame)})
        body = record({0: common, 1: struct.pack('<2d', .75, 1.05),
                       5: struct.pack('<2d', .75, .05), 6: b'\1\0\0\0'})
        # 0x900d is a valid object ID; its first bytes equal the end sentinel.
        raw = struct.pack('<III', 0x900d, 19, len(body)) + body + b'\r\x90'
        a, = gradient_appearances(raw, {19: 'AGradientBrush'})
        self.assertEqual(a['brush_id'], 176)
        self.assertEqual(a['matrix'], list(frame))
        g = {'kind': 'linear', 'reference_start': [208., -34.],
             'stops': [{'offset': 0., 'color': '#ffffff'}, {'offset': 1., 'color': '#d8e2e1'}]}
        node = ET.fromstring(gradient_svg(g, a, 'source-176'))
        self.assertEqual(node.attrib['x1'], '0.75')
        self.assertEqual(node.attrib['gradientTransform'], 'matrix(8 0 0 3 625 404)')
        self.assertNotIn('208', ET.tostring(node).decode())

    def test_radial_gradient_retains_nonuniform_page_frame(self):
        g = {'kind': 'radial', 'stops': [{'offset': 0., 'color': '#ffffff'},
                                       {'offset': 1., 'color': '#7f7f7f'}]}
        a = {'start': [.5, .5], 'end': [1., .5], 'matrix': [5., 0., 0., 2., 20., 30.]}
        node = ET.fromstring(gradient_svg(g, a, 'a'))
        self.assertEqual(node.tag, 'radialGradient')
        self.assertEqual(float(node.attrib['r']), .5)
        self.assertEqual(node.attrib['gradientTransform'], 'matrix(5 0 0 2 20 30)')
        self.assertIsNone(gradient_svg({'kind': 'unsupported'}, a, 'a'))

    def test_malformed_record_and_nonfinite_coordinates_fail(self):
        with self.assertRaises(ValueError):
            display_color(record({0: b'\0\0\0'}))
        with self.assertRaises(ValueError):
            gradient_appearances(b'abc', {19: 'AGradientBrush'})


if __name__ == '__main__':
    unittest.main()
