"""Completed FS tasks remain reviewable while the rest of a batch runs."""
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import patch

from review_gallery import ReviewGallery
from test_reviewed_exports import fixture
from ui import LiveryApp


class IndependentTaskReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        with patch.object(LiveryApp, '_refresh_ai'):
            self.app = LiveryApp(self.root)
        self.addCleanup(self.close_app)
        self.app.output.set(str(self.folder / 'output'))
        self.result = dict(fixture(self.folder), status='review')
        self.ready = self.app.tasks.insert('', 'end', values=('completed.fs', '候选需复核'))
        self.app.sources[self.ready] = Path(self.result['source'])
        self.app.results[self.ready] = self.result
        self.app.current = self.ready

    def close_app(self):
        self.app._set_busy(False)
        self.app._close()

    def test_completed_task_opens_with_mixed_batch_states(self):
        for status in ('处理中', '待处理', '失败'):
            with self.subTest(other_task_status=status):
                other = self.app.tasks.insert('', 'end', values=('other.fs', status))
                self.app._set_busy(True)
                with patch('review_gallery.ReviewGallery') as gallery, \
                        patch('ui.messagebox.showinfo') as info:
                    self.app.review_button.invoke()
                gallery.assert_called_once_with(
                    self.root, self.result, self.folder / 'output' / '已复核成品', None)
                info.assert_not_called()
                self.assertTrue(self.app.busy)
                self.app.tasks.delete(other)

    def test_selected_unfinished_task_cannot_use_another_tasks_result(self):
        other = self.app.tasks.insert('', 'end', values=('other.fs', '处理中'))
        self.app.current = other
        self.app._set_busy(True)
        with patch('review_gallery.ReviewGallery') as gallery, \
                patch('ui.messagebox.showinfo') as info:
            self.app.review_button.invoke()
        gallery.assert_not_called()
        info.assert_called_once()

    def test_actual_gallery_exports_only_selected_fs_while_batch_is_busy(self):
        other = self.app.tasks.insert('', 'end', values=('other.fs', '处理中'))
        self.app._set_busy(True)
        with patch('review_gallery.ReviewGallery', wraps=ReviewGallery) as opened:
            self.app.review_button.invoke()
        gallery = next(w for w in self.root.winfo_children() if isinstance(w, ReviewGallery))
        gallery.withdraw()
        self.addCleanup(gallery.destroy)
        self.assertIs(opened.call_args.args[1], self.result)
        for category, ids in self.result['component_categories'].items():
            gallery._switch_category(category)
            if ids:
                gallery.current_candidate = {'selected_ids': ids}
                gallery._confirm()
            else:
                gallery._absent()
        with patch('review_gallery.messagebox.showinfo') as info, \
                patch('review_gallery.messagebox.showerror') as error:
            gallery.export_button.invoke()
            deadline = time.monotonic() + 10
            while gallery.busy and time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.01)
            self.assertFalse(gallery.busy, 'Export did not complete')
            error.assert_not_called()
            info.assert_called_once()
        self.assertTrue(self.app.busy)
        self.assertEqual(self.app.tasks.set(other, 'status'), '处理中')
        output = self.folder / 'output' / '已复核成品'
        self.assertEqual([p.name for p in output.iterdir()], [Path(self.result['source']).stem])
        record = json.loads(next(output.rglob('复核记录.json')).read_text(encoding='utf8'))
        self.assertEqual(record['categories']['彩条']['selected_ids'],
                         self.result['component_categories']['彩条'])
        self.assertTrue(list(output.rglob('*.svg')))
        self.assertTrue(list(output.rglob('*.png')))


if __name__ == '__main__':
    unittest.main()
