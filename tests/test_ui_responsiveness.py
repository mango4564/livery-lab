"""Offline checks for preview scheduling, stale results and bounded redraws."""
import threading
import tempfile
from pathlib import Path
import time
import tkinter as tk
import unittest
from tests.tk_lifecycle import TkTestCase
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from ui import LiveryApp
from vector_preview import VectorImage


class ResponsivenessTests(TkTestCase):
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

    def test_loading_is_distinct_from_empty_and_error_stops_progress(self):
        started, release = threading.Event(), threading.Event()
        def prepare(*args, **kwargs):
            started.set()
            release.wait(3)
            raise ValueError('broken fixture')
        with patch.object(self.app, '_prepare_previews', side_effect=prepare):
            self.app.current = 'task'
            self.app._request_previews({'source': 'fixture'})
            self.wait_for(started.is_set)
            viewer = self.app.preview_viewers['result_png']
            self.assertTrue(viewer._loading)
            viewer.draw()
            text = viewer.canvas.itemcget(viewer.canvas.find_withtag('preview')[-1], 'text')
            self.assertIn('正在加载', text)
            locator_text = self.app.source_locator.canvas.itemcget(
                self.app.source_locator.canvas.find_all()[-1], 'text')
            self.assertIn('正在加载', locator_text)
            release.set()
            self.wait_for(lambda: not viewer._loading)
            self.assertIn('加载失败', viewer._empty_message)

    def test_raster_appears_before_slow_vector_preparation(self):
        started, release = threading.Event(), threading.Event()
        def prepare(*args, **kwargs):
            started.set()
            release.wait(3)
            images, messages = kwargs['prepared_images']
            return (('fixture', None), None, {}, images, messages)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'preview.png'
            Image.new('RGBA', (20, 10), 'blue').save(path)
            with patch.object(self.app, '_prepare_previews', side_effect=prepare):
                self.app.current = 'task'
                self.app._request_previews({'preview_png': str(path)})
                self.wait_for(started.is_set)
                viewer = self.app.preview_viewers['preview_png']
                self.wait_for(lambda: viewer.image is not None)
                self.assertTrue(viewer._loading)
                self.assertEqual(viewer.image.getpixel((0, 0)), (0, 0, 255, 255))
                release.set()
                self.wait_for(lambda: not viewer._loading)

    def test_preview_is_applied_before_slow_locator(self):
        started, release = threading.Event(), threading.Event()
        image = Image.new('RGBA', (10, 10), 'green')
        def locator(*args):
            started.set()
            release.wait(3)
            return None
        assets = (('fixture', None), None, {}, {'result_png': image}, [])
        with patch.object(self.app, '_prepare_previews', return_value=assets), \
                patch('ui.SourceLocatorRenderer.prepare', side_effect=locator):
            self.app.current = 'task'
            self.app._request_previews({'source': 'fixture'})
            self.wait_for(started.is_set)
            viewer = self.app.preview_viewers['result_png']
            self.wait_for(lambda: viewer.image is image)
            self.assertIsNone(self.app.source_locator.photo)
            self.assertFalse(viewer._loading)
            release.set()

    def test_first_vector_frame_has_progress_and_stale_frame_cannot_end_loading(self):
        viewer = self.app.preview_viewers['preview_png']
        vector = VectorImage('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>')
        started, release = threading.Event(), threading.Event()
        original = vector.render
        def render(*args):
            started.set()
            release.wait(3)
            return original(*args)
        with patch.object(vector, 'render', side_effect=render):
            viewer.set_image(None, vector)
            viewer.draw()
            self.wait_for(started.is_set)
            self.assertTrue(viewer._loading)
            viewer.set_loading('正在加载另一份文件…')
            release.set()
            self.wait_for(lambda: viewer._render_after is None)
            self.assertTrue(viewer._loading)
            self.assertIsNone(viewer.photo)


if __name__ == '__main__':
    unittest.main()
