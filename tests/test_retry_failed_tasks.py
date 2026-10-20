"""Offline regression checks for retrying failed FS tasks without rerunning peers."""
from pathlib import Path
import tempfile
import tkinter as tk
from unittest.mock import patch

from tests.tk_lifecycle import TkTestCase
from ui import LiveryApp


class RetryFailedTasksTests(TkTestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        with patch.object(LiveryApp, '_refresh_ai'):
            self.app = LiveryApp(self.root)
        self.addCleanup(self.close_app)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app.output.set(self.temp.name)

    def close_app(self):
        self.app._set_busy(False)
        self.app._close()

    def add_task(self, name, status):
        item = self.app.tasks.insert('', 'end', values=(name, status))
        self.app.sources[item] = Path(self.temp.name) / name
        return item

    def test_retry_only_failed_and_uses_current_options(self):
        app = self.app
        ready = self.add_task('ready.fs', '候选需复核')
        pending = self.add_task('pending.fs', '待处理')
        failed = self.add_task('failed.fs', '失败')
        result = {'selected_ids': [], 'status': 'review'}
        app.results[ready] = result
        app.selections[ready] = {'manual-choice'}
        app.concurrency.set(2)
        app.use_ai.set(True)
        app._set_busy(False)
        with patch('ui.threading.Thread') as thread, patch.object(app, '_refresh_ai'):
            app.retry_button.invoke()
            args = thread.call_args.kwargs['args']
            self.assertEqual(args, ([(failed, app.sources[failed])], Path(self.temp.name),
                                    True, True, '', 2, 'components'))
            thread.return_value.start.assert_called_once()
            self.assertTrue(app.busy)
            self.assertTrue(app.retry_button.instate(['disabled']))
            app._retry_failed()
            self.assertEqual(thread.call_count, 1)
        self.assertEqual(app.tasks.set(failed, 'status'), '等待重试')
        self.assertEqual(app.tasks.set(pending, 'status'), '待处理')
        self.assertIs(app.results[ready], result)
        self.assertEqual(app.selections[ready], {'manual-choice'})
        self.assertEqual(float(app.progress['maximum']), 1)

    def test_failed_retry_can_retry_again_then_success_disables_button(self):
        app = self.app
        failed = self.add_task('failed.fs', '失败')
        jobs = [(failed, app.sources[failed])]
        for outcome in (RuntimeError('stream interrupted'), {'status': 'review', 'selected_ids': []}):
            app._set_busy(True)
            app.stop_event.clear()
            with patch('engine.process_file', side_effect=[outcome]) as process, \
                    patch.object(app, '_request_previews'), patch.object(app, '_update_groups'):
                app._batch_worker(jobs, Path(self.temp.name), force=True)
                while not app.events.empty():
                    app._drain()
                process.assert_called_once()
            self.assertFalse(app.busy)
            if isinstance(outcome, Exception):
                self.assertEqual(app._failed_jobs(), jobs)
                self.assertTrue(app.retry_button.instate(['!disabled']))
            else:
                self.assertEqual(app._failed_jobs(), [])
                self.assertTrue(app.retry_button.instate(['disabled']))

    def test_removing_last_failed_task_disables_retry(self):
        failed = self.add_task('failed.fs', '失败')
        self.app._set_busy(False)
        self.assertTrue(self.app.retry_button.instate(['!disabled']))
        self.app.tasks.selection_set(failed)
        with patch.object(self.app, '_request_previews'):
            self.app._remove()
        self.assertTrue(self.app.retry_button.instate(['disabled']))
        with patch('ui.threading.Thread') as thread:
            self.app._retry_failed()
            thread.assert_not_called()
