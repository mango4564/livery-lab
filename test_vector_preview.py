"""Offline vector sharpness and viewport allocation regression checks."""
import unittest

from vector_preview import VectorImage


class VectorPreviewTests(unittest.TestCase):
    def setUp(self):
        self.vector = VectorImage(
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<path d="M20.3 0 L20.3 100 L80 100 L80 0 Z" fill="red"/></svg>')

    def test_edge_stays_sharp_at_high_zoom(self):
        for scale in (1, 8, 32):
            with self.subTest(scale=scale):
                tile, _, _ = self.vector.render(scale, (30 - 20 * scale, 0), (300, 200))
                alpha = tile.getchannel('A')
                row = [alpha.getpixel((x, 50)) for x in range(tile.width)]
                self.assertLessEqual(sum(0 < value < 255 for value in row), 2)
                self.assertIn(0, row)
                self.assertIn(255, row)

    def test_large_page_only_allocates_viewport(self):
        vector = VectorImage(
            '<svg xmlns="http://www.w3.org/2000/svg" width="6000" height="4000">'
            '<path d="M0 0 H6000 V4000 H0 Z" fill="blue"/></svg>')
        tile, x, y = vector.render(32, (-80000.25, -50000.5), (800, 500))
        self.assertLessEqual(tile.width, 802)
        self.assertLessEqual(tile.height, 502)
        self.assertLessEqual(abs(x), 1)
        self.assertLessEqual(abs(y), 1)
        self.assertEqual(tile.getpixel((400, 250)), (0, 0, 255, 255))

    def test_outside_page(self):
        self.assertIsNone(self.vector.render(8, (500, 500), (100, 100)))

    def test_transparency_survives(self):
        tile, _, _ = self.vector.render(1, (0, 0), (100, 100))
        self.assertEqual(tile.getpixel((0, 50)), (0, 0, 0, 0))
        self.assertEqual(tile.getpixel((50, 50)), (255, 0, 0, 255))

    def test_gradient_is_not_rendered_black(self):
        vector = VectorImage(
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<defs><linearGradient id="glass"><stop offset="0" stop-color="#ffffff"/>'
            '<stop offset="1" stop-color="#d8e2e1"/></linearGradient></defs>'
            '<path d="M0 0H100V100H0Z" fill="url(#glass)"/></svg>')
        tile, _, _ = vector.render(1, (0, 0), (100, 100))
        self.assertTrue(all(v > 200 for v in tile.getpixel((50, 50))[:3]))
        self.assertNotEqual(tile.getpixel((5, 50)), tile.getpixel((95, 50)))

    def test_native_wireframe_stays_one_pixel_at_any_zoom(self):
        vector = VectorImage(
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
            '<path d="M0 20H100" fill="none" stroke="red" stroke-width="1" '
            'data-fs-hairline="true" vector-effect="non-scaling-stroke"/></svg>')
        for scale in (1, 8, 32):
            tile, _, _ = vector.render(scale, (0, 40 - 20 * scale), (100, 100))
            coverage = sum(tile.getpixel((50, y))[3] / 255 for y in range(100))
            self.assertAlmostEqual(coverage, 1, delta=.02)


if __name__ == '__main__':
    unittest.main()
