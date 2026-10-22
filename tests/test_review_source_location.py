"""Offline Tk checks for source-location overlays during pattern review."""
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from tests.tk_lifecycle import TkTestCase
from unittest.mock import patch

from PIL import ImageTk
from review_gallery import ReviewGallery
from source_locator import source_rectangle
from test_reviewed_exports import fixture


class ReviewSourceLocationTests(TkTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.result = fixture(Path(self.temp.name))
        self.result['component_candidates'] = [
            {'id': 'alternate', 'selected_ids': ['5'], 'category_hints': ['彩条']}]
        mock = patch('core.fs_scene.source_preview_objects', return_value=self.result['objects'])
        self.reader = mock.start()
        self.addCleanup(mock.stop)
        self.gallery = ReviewGallery(self.root, self.result, Path(self.temp.name)/'out')
        self.gallery.withdraw()
        self.addCleanup(self.close_gallery)

    def close_gallery(self):
        self.gallery.destroy()
        time.sleep(.25)  # Allow the data-only worker to exit before removing mocks.

    def wait_location(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.root.update()
            if self.gallery.source_locator.photo is not None:
                return
            time.sleep(.02)
        self.fail('source-location preview did not finish')

    def assert_location(self, oid):
        self.wait_location()
        locator = self.gallery.source_locator
        image = ImageTk.getimage(locator.photo)
        bounds = next(o['bbox'] for o in self.result['objects'] if o['id'] == oid)
        box = source_rectangle(locator._viewbox, bounds, image.size, locator._viewport)
        red = [(x, y) for y in range(image.height) for x in range(image.width)
               if image.getpixel((x, y))[:3] == (239, 48, 56)]
        self.assertTrue(red)
        for axis in (0, 1):
            center = (min(p[axis] for p in red)+max(p[axis] for p in red))/2
            self.assertAlmostEqual(center, (box[axis]+box[axis+2])/2, delta=2)

    def test_candidates_and_added_patterns_follow_current_selection(self):
        g = self.gallery
        g._switch_category('彩条')
        g._confirm()
        self.assert_location('1')
        g.tree.selection_set('1')
        g._preview()
        self.assert_location('5')
        g.pattern_list.selection_set(0)
        g._preview_pattern()
        self.assert_location('5')
        self.assertEqual(g.choices['彩条'], ['5'])
        self.assertEqual(self.reader.call_count, 1)

    def test_empty_category_clears_location_and_ignores_stale_result(self):
        g = self.gallery
        self.wait_location()
        old = g._locator_generation
        g._switch_category('车漆')
        self.assertIsNone(g.current_candidate)
        self.assertIsNone(g.source_locator.photo)
        g.events.put(('locator', (old, None, None, None, 'stale candidate')))
        deadline = time.monotonic() + .4
        while time.monotonic() < deadline:
            self.root.update()
            time.sleep(.02)
        self.assertNotEqual(g.source_locator.heading.cget('text'), 'stale candidate')
        self.assertIsNone(g.source_locator.photo)


if __name__ == '__main__':
    unittest.main()
