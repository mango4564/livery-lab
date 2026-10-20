"""Candidate hover interaction and source-coordinate locator regressions."""
import tempfile
from pathlib import Path
import tkinter as tk
import time
import unittest
from tests.tk_lifecycle import TkTestCase
from unittest.mock import patch

from geometry import svg_document
from hover_choice import HoverChoice
from source_locator import SourceLocator, source_rectangle, visible_source_bounds
from ui import LiveryApp
from vector_preview import VectorDocument, VectorImage


class ReviewPreviewTests(TkTestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry('1280x880+0+0')
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)

    def tearDown(self):
        for timer in self.root.tk.call('after', 'info'):
            script = self.root.tk.call('after', 'info', timer)[0]
            if script in (self.root._tclCommands or []):
                self.root.after_cancel(timer)
        if hasattr(self, 'app'):
            self.app._close()
            self.app._preview_thread.join(timeout=3)
        else:
            self.root.destroy()
        self.assertEqual(self.errors, [])

    def test_hover_opens_and_switches_native_list_without_clicking(self):
        seen = []
        choice = HoverChoice(self.root, state='readonly', values=['Left', 'Right'],
                             on_hover=lambda: seen.append(choice.current()))
        choice.pack()
        self.root.update()
        choice.event_generate('<Enter>')
        self.root.update()
        self.assertTrue(int(choice.tk.call('winfo', 'ismapped', choice._popup)))
        box = choice.tk.call(choice._listbox, 'bbox', 1)
        x, y, _, height = map(int, box)
        choice.tk.call('event', 'generate', choice._listbox, '<Motion>', '-x', x+2, '-y', y+height//2)
        self.root.update()
        self.assertEqual(seen, [1])
        self.assertEqual(choice.current(), 1)
        choice._motion(x+3, y+height//2)
        self.assertEqual(seen, [1])
        choice.tk.call('ttk::combobox::Unpost', choice)

    def test_locator_uses_y_up_coordinates_with_nonzero_origin(self):
        self.assertEqual(source_rectangle((100, 200, 400, 200), (140, 340, 220, 380), (200, 100)),
                         (20, 10, 60, 30))
        self.assertEqual(source_rectangle((100, 200, 400, 200), (140, 340, 220, 380), (200, 200)),
                         (20, 60, 60, 80))

    def test_oversized_white_canvas_is_cropped_and_selection_stays_aligned(self):
        from PIL import ImageTk
        objects = [dict(id='blank', bbox=[-1000, -1000, 9000, 9000], fill='#ffffff',
                        d='M-1000 -1000H9000V9000H-1000Z'),
                   dict(id='remote-note', bbox=[150, 8000, 160, 8010], fill='#cccccc',
                        d='M150 8000H160V8010H150Z'),
                   dict(id='art', bbox=[100, 200, 500, 400], fill='#167adf',
                        d='M100 200H500V400H100Z')]
        page = dict(objects=objects, error=None, image=VectorImage(svg_document(objects, background='#ffffff')))
        locator = SourceLocator(self.root)
        locator.set_source(page, [100, 200, 500, 400])
        self.assertLess(locator._viewbox[2], 500)
        self.assertLess(locator._viewbox[3], 300)
        box = source_rectangle(locator._viewbox, [100, 200, 500, 400], (216, 124), locator._viewport)
        self.assertGreater(box[2] - box[0], 175)
        image = ImageTk.getimage(locator.photo)
        self.assertEqual(image.getpixel((108, 62))[:3], (22, 122, 223))
        self.assertEqual(image.getpixel((int(box[0])+1, 62))[:3], (239, 48, 56))
        # A selected white object outside the visible artwork must not disappear.
        locator.set_source(page, [1000, 200, 1200, 400])
        self.assertGreater(locator._viewbox[0] + locator._viewbox[2], 1200)

    def test_blank_artwork_has_safe_fallback_and_tiny_selection_is_visible(self):
        obj = dict(id='white', bbox=[0, 0, 5000, 5000], fill='#ffffff', d='M0 0H5000V5000H0Z')
        vector = VectorImage(svg_document([obj], background='#ffffff'))
        self.assertIsNone(visible_source_bounds(vector, (-75, -75, 5150, 5150)))
        locator = SourceLocator(self.root)
        locator.set_source(dict(objects=[obj], image=vector), [100, 100, 110, 110])
        self.assertLess(locator._viewbox[2], 12)
        locator.set_source(dict(objects=[obj], image=vector), None)
        self.assertIsNotNone(locator.photo)

    def test_candidate_switch_updates_selection_result_and_locator_only(self):
        with patch.object(LiveryApp, '_refresh_ai'):
            app = LiveryApp(self.root)
        self.app = app
        objects = [dict(id='1', bbox=[100, 200, 300, 300], fill='#167adf',
                        d='M100 200H300V300H100Z', score=1),
                   dict(id='2', bbox=[500, 500, 600, 600], fill='#167adf',
                        d='M500 500H600V600H500Z', score=1)]
        svg = svg_document(objects, background='#ffffff')
        page = dict(name='Page 1', label='Page 1', objects=objects, error=None, image=VectorImage(svg))
        app._source_vector = VectorDocument([page])
        app._source_preview_key = ('fixture', 'digest')
        app.current = 'task'
        with tempfile.TemporaryDirectory() as tmp:
            groups = []
            for obj in objects:
                path = Path(tmp) / f'{obj["id"]}.svg'
                path.write_text(svg_document([obj]), encoding='utf8')
                groups.append(dict(label=obj['id'], selected_ids=[obj['id']], svg=str(path), png=None))
            app.results['task'] = dict(source='fixture', source_sha256='digest',
                                       objects=objects, views=groups, selected_ids=['1'])
            app.selections['task'] = {'1'}
            app._update_groups()
            self.root.update()
            with patch.object(app.preview_viewers['preview_png'], 'set_image') as source_update:
                app.group_choice.current(1)
                app._choose_group()
                deadline = time.monotonic() + 3
                while (app.preview_viewers['result_png'].vector_image is None
                       or app.source_locator.photo is None):
                    self.root.update()
                    if time.monotonic() > deadline:
                        self.fail('Candidate preview did not finish loading')
                    time.sleep(0.005)
                self.assertEqual(app.selections['task'], {'2'})
                self.assertEqual(app.results['task']['selected_ids'], ['1'])
                self.assertEqual(app.previews.select(), str(app.preview_viewers['result_png']))
                source_update.assert_not_called()
                self.assertIsNotNone(app.source_locator.photo)
                self.assertEqual(app.preview_viewers['result_png'].vector_image.width, 1600)
            # Empty and failed source pages clear a stale locator without crashing.
            app.source_locator.set_source(None, None)
            item = app.source_locator.canvas.find_all()[-1]
            self.assertIn('不可用', app.source_locator.canvas.itemcget(item, 'text'))


if __name__ == '__main__':
    unittest.main()
