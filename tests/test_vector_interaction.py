"""Retained vectors and visible Tk rendering, without network or FS fixtures."""
import threading
import gc
import time
import tkinter as tk
import unittest
from tests.tk_lifecycle import TkTestCase
from unittest.mock import patch

from PIL import ImageChops

from image_preview import ImagePreview
from vector_preview import VectorImage


class RetainedVectorTests(TkTestCase):
    def test_picture_retains_viewbox_gradients_transforms_and_source_order(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="240" height="160" viewBox="10 20 120 80">'
               '<defs><linearGradient id="g"><stop stop-color="red"/>'
               '<stop offset="1" stop-color="blue"/></linearGradient></defs>'
               '<rect x="10" y="20" width="120" height="80" fill="white"/>'
               '<g transform="translate(10 90) scale(1 -1)"><g transform="translate(5 0)">'
               '<path d="M0 10H90V60H0Z" fill="url(#g)"/>'
               '<path d="M0 30H90" transform="matrix(1 0 0 1 0 0)" fill="none" '
               'stroke="#00ff00" stroke-width="0.5" data-fs-hairline="true"/>'
               '<path d="M30 10H60V60H30Z" fill="#ffff00"/>'
               '</g></g></svg>')
        vector = VectorImage(svg)
        self.assertIsNotNone(vector._picture)
        actual = vector.render(1, (0, 0), (240, 160))[0]
        # Compare against the original SVG importer with equivalent 1px stroke.
        original = VectorImage(svg.replace('data-fs-hairline="true"', ''))
        expected = original.render(1, (0, 0), (240, 160))[0]
        self.assertIsNone(ImageChops.difference(actual, expected).getbbox(alpha_only=False))
        with patch.object(VectorImage, '_parse', side_effect=AssertionError('zoom reparsed SVG')):
            for scale in (0.5, 2, 8):
                vector.render(scale, (0, 0), (240, 160))

    def test_cosmetic_stroke_survives_nonuniform_object_transform(self):
        vector = VectorImage('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<path d="M0 5H100" transform="scale(1 4)" fill="none" stroke="#ff0000" '
            'data-fs-hairline="true"/></svg>')
        self.assertIsNotNone(vector._picture)
        for scale in (1, 8, 32):
            tile = vector.render(scale, (0, 40-20*scale), (100, 100))[0]
            self.assertAlmostEqual(sum(tile.getpixel((50, y))[3]/255 for y in range(100)), 1, delta=.02)

    def test_unsupported_svg_keeps_importer(self):
        vector = VectorImage('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<g opacity="0.5"><path d="M0 20H100" fill="none" stroke="#ff0000" '
            'data-fs-hairline="true"/></g></svg>')
        self.assertIsNone(vector._picture)
        self.assertIsNotNone(vector.render(2, (0, 0), (100, 100)))

    def test_absolute_arc_commands_record(self):
        vector = VectorImage('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<path d="M20 50 A30 20 0 1 1 80 50 A30 20 0 1 1 20 50 Z" fill="none" '
            'stroke="#ff0000" data-fs-hairline="true"/></svg>')
        self.assertIsNotNone(vector._picture)
        self.assertIsNotNone(vector.render(1, (0, 0), (100, 100))[0].getbbox())

    def test_filled_cosmetic_stroke_and_commands_after_close(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<path d="M10 10 L40 40 L10 40 Z H70 V20" fill="#ff0000" fill-rule="evenodd" '
            'stroke="#0000ff" stroke-width="1" data-fs-hairline="true"/></svg>')
        vector = VectorImage(svg)
        self.assertIsNotNone(vector._picture)
        expected = VectorImage(svg.replace('data-fs-hairline="true"', '')).render(1, (0, 0), (100, 100))[0]
        actual = vector.render(1, (0, 0), (100, 100))[0]
        self.assertIsNone(ImageChops.difference(actual, expected).getbbox(alpha_only=False))


class VisibleInteractionTests(TkTestCase):
    def setUp(self):
        gc.collect()  # Dispose previous Tk cycles on their owning thread.
        self.root = tk.Tk()
        self.root.geometry('520x380')
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.viewer = ImagePreview(self.root)
        self.viewer.pack(fill='both', expand=True)
        self.root.update()

    def tearDown(self):
        worker = self.viewer._render_thread
        self.root.destroy()
        if worker is not None:
            worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(self.errors, [])
        self.viewer = self.root = None
        gc.collect()

    def wait_for(self, predicate):
        deadline = time.monotonic()+4
        while not predicate():
            if time.monotonic() > deadline:
                self.fail('Timed out waiting for visible render')
            self.root.update()
            time.sleep(.002)

    def test_visible_slow_vector_keeps_tk_alive_latest_scale_wins_and_overlay_survives(self):
        vector = VectorImage('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
                             '<path d="M0 0H100V100H0Z" fill="red"/></svg>')
        started, release = threading.Event(), threading.Event()
        real_render = vector.render
        calls = []
        def render(scale, *args):
            calls.append(scale)
            if len(calls) == 1:
                started.set()
                release.wait(3)
            return real_render(scale, *args)
        with patch.object(vector, 'render', side_effect=render):
            self.viewer.set_image(None, vector)
            self.wait_for(started.is_set)
            ticks = []
            self.root.after(0, lambda: ticks.append(True))
            self.root.update()
            self.assertTrue(ticks)
            for scale in (2, 3, 4):
                self.viewer.set_scale(scale)
                self.viewer.draw()
            overlay = self.viewer.canvas.create_rectangle(5, 5, 20, 20, tags='selection')
            release.set()
            self.wait_for(lambda: self.viewer._frame is not None and self.viewer._frame[1] == 4)
            self.assertEqual(calls, [calls[0], 4])
            self.assertTrue(self.viewer.canvas.type(overlay))
            self.assertEqual(self.viewer.canvas.find_all()[-1], overlay)

    def test_destroy_during_render_never_calls_tk_from_worker(self):
        vector = VectorImage('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>')
        started, release = threading.Event(), threading.Event()
        def blocked(*args):
            started.set()
            release.wait(3)
            return None
        with patch.object(vector, 'render', side_effect=blocked):
            self.viewer.set_image(None, vector)
            self.wait_for(started.is_set)
            worker = self.viewer._render_thread
            self.viewer.destroy()
            release.set()
            worker.join(2)
            self.assertFalse(worker.is_alive())

    def test_clear_during_pending_render_discards_frame_and_stops_polling(self):
        vector = VectorImage('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>')
        started, release = threading.Event(), threading.Event()
        real_render = vector.render
        def blocked(*args):
            started.set()
            release.wait(3)
            return real_render(*args)
        with patch.object(vector, 'render', side_effect=blocked):
            self.viewer.set_image(None, vector)
            self.wait_for(started.is_set)
            self.viewer.set_image(None)
            self.viewer.draw()
            release.set()
            self.wait_for(lambda: self.viewer._render_after is None)
            self.assertIsNone(self.viewer._frame)
            self.assertIsNone(self.viewer.image)


if __name__ == '__main__':
    unittest.main()
