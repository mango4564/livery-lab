"""Offline checks for preview scheduling, stale results and bounded redraws."""
import threading
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from ui import LiveryApp


class ResponsivenessTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        with patch.object(LiveryApp, '_refresh_ai'):
            self.app = LiveryApp(self.root)

    def tearDown(self):
        self.app._close()
        if self.app._preview_thread:
            self.app._preview_thread.join(timeout=3)
            self.assertFalse(self.app._preview_thread.is_alive())
        self.assertEqual(self.errors, [])

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            if time.monotonic() > deadline:
                self.fail('Timed out waiting for preview worker')
            self.root.update()
            time.sleep(0.005)

    def test_slow_preview_keeps_tk_responsive_and_discards_obsolete_tasks(self):
        started, release = threading.Event(), threading.Event()
        seen = []
        def prepare(result, *args, **kwargs):
            seen.append(result['source'])
            if result['source'] == 'first':
                started.set()
                if not release.wait(3):
                    raise RuntimeError('Test did not release blocked worker')
            return ((result['source'], None), None, {}, {}, [])

        with patch.object(self.app, '_prepare_previews', side_effect=prepare), \
                patch.object(self.app, '_load_previews') as apply:
            self.app.current = 'first'
            self.app._request_previews({'source': 'first'})
            self.wait_for(started.is_set)
            ticks = []
            self.root.after(0, lambda: ticks.append(True))
            self.root.update()
            self.assertTrue(ticks, 'Tk must run callbacks while parsing is blocked')
            for source in ('second', 'third'):
                self.app.current = source
                self.app._request_previews({'source': source})
            release.set()
            self.wait_for(lambda: apply.called)
            self.assertEqual(seen, ['first', 'third'])
            self.assertEqual(apply.call_count, 1)
            self.assertEqual(apply.call_args.args[0]['source'], 'third')

    def test_revisiting_document_reuses_source_and_eviction_is_bounded(self):
        received = []
        vectors = {name: SimpleNamespace(pages=[]) for name in 'abcd'}
        def prepare(result, source, vector, **kwargs):
            received.append((result['source'], vector))
            key = (result['source'], 'digest')
            return (key, vector or vectors[result['source']], {}, {}, [])
        with patch.object(self.app, '_prepare_previews', side_effect=prepare):
            for name in 'abacda':
                self.app.current = name
                self.app._request_previews({'source': name, 'source_sha256': 'digest'})
                self.wait_for(lambda: not self.app.events.empty())
                # No Tk application needed to exercise the worker's LRU cache.
                self.assertEqual(self.app.events.get_nowait()[0], 'previews')
            self.assertIs(received[2][1], vectors['a'])
            self.assertIs(received[-1][1], vectors['a'])
            self.app.current = 'b'
            self.app._request_previews({'source': 'b', 'source_sha256': 'digest'})
            self.wait_for(lambda: not self.app.events.empty())
            self.app.events.get_nowait()
            self.assertIsNone(received[-1][1], 'Least recently used document must be evicted')

    def test_repeated_selection_does_not_reload_same_result(self):
        item = self.app.tasks.insert('', 'end', values=('fixture', 'ready'))
        self.app.tasks.selection_set(item)
        self.app.results[item] = {'objects': []}
        with patch.object(self.app, '_request_previews') as load:
            self.app._task_selected()
            self.app._task_selected()
            self.root.update()
            self.assertEqual(load.call_count, 1)

    def test_draw_requests_coalesce_and_hidden_preview_does_not_render(self):
        viewer = self.app.preview_viewers['preview_png']
        with patch.object(viewer, 'draw') as draw:
            for _ in range(100):
                viewer.request_draw()
            timer = viewer._draw_after
            self.assertIsNotNone(timer)
            self.wait_for(lambda: viewer._draw_after is None)
            draw.assert_not_called()
            with patch.object(viewer.canvas, 'winfo_ismapped', return_value=True):
                for _ in range(100):
                    viewer.request_draw()
                self.wait_for(lambda: viewer._draw_after is None)
            self.assertEqual(draw.call_count, 1)

    def test_missing_result_image_clears_previous_candidate(self):
        app = self.app
        app.preview_images['result_png'] = Image.new('RGBA', (10, 10), 'red')
        app._load_previews({}, result_only=True,
                           assets=((None, None), None, {}, {}, []))
        self.assertNotIn('result_png', app.preview_images)
        self.assertIsNone(app.preview_viewers['result_png'].image)


if __name__ == '__main__':
    unittest.main()
