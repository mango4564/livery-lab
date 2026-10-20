"""Offline cancellation checks using real disposable workers and a hidden Tk UI."""
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import engine
from tests.tk_lifecycle import TkTestCase
from ui import LiveryApp


class WorkerCancellationTests(unittest.TestCase):
    def test_cancel_kills_and_reaps_running_worker(self):
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as tmp:
            ready = Path(tmp) / 'ready'
            cmd = [sys.executable, '-c',
                   'import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(60)',
                   str(ready)]
            children = []
            popen = subprocess.Popen

            def launch(*args, **kwargs):
                child = popen(*args, **kwargs)
                children.append(child)
                return child

            def stop_when_running():
                deadline = time.monotonic() + 3
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                cancel.set()

            stopper = threading.Thread(target=stop_when_running)
            stopper.start()
            try:
                with patch.object(engine.subprocess, 'Popen', side_effect=launch):
                    with self.assertRaises(engine.ExtractionCancelled):
                        engine._execute_worker(cmd, 10, cancel)
            finally:
                stopper.join(4)
            self.assertTrue(ready.exists(), 'Cancellation must interrupt a running child')
            self.assertIsNotNone(children[0].poll())
            self.assertTrue(children[0].stdout.closed)

    def test_timeout_reaps_worker_and_success_captures_output(self):
        cancel = threading.Event()
        with self.assertRaises(subprocess.TimeoutExpired):
            engine._execute_worker([sys.executable, '-c', 'import time; time.sleep(60)'], 0.15, cancel)
        result = engine._execute_worker([sys.executable, '-c', 'print("ready")'], 5, cancel)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), 'ready')

    def test_cancelled_run_does_not_publish_manifest_or_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'sample.fs'
            source.write_bytes(b'offline fixture')
            output = Path(tmp) / 'out'
            with patch.object(engine, '_execute_worker', side_effect=engine.ExtractionCancelled):
                with self.assertRaises(engine.ExtractionCancelled):
                    engine.process_file(source, output, cancel_event=threading.Event())
            self.assertFalse(list(output.rglob('manifest.json')))
            self.assertFalse((output / '.cache').exists())
            self.assertEqual(len(list(output.rglob('failure.json'))), 1)


class ExtractionCloseTests(TkTestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        with patch.object(LiveryApp, '_refresh_ai'):
            self.app = LiveryApp(self.root)
        self.addCleanup(self.app._close)

    def test_close_while_busy_destroys_window_and_requests_cancellation(self):
        self.app._set_busy(True)
        self.app._close()
        self.assertTrue(self.app.stop_event.is_set())
        with self.assertRaises(tk.TclError):
            self.root.winfo_exists()
        self.app._close()  # Repeated close/fixture cleanup is harmless.

    def test_stop_interrupts_running_job_and_skips_queued_job(self):
        started = threading.Event()
        app = self.app
        for name in ('first.fs', 'second.fs'):
            item = app.tasks.insert('', 'end', values=(name, '待处理'))
            app.sources[item] = Path(name)
        calls = []

        def extract(source, output, *, cancel_event, **kwargs):
            calls.append(source)
            started.set()
            if not cancel_event.wait(3):
                raise RuntimeError('Cancellation was not delivered')
            raise engine.ExtractionCancelled()

        with tempfile.TemporaryDirectory() as tmp, patch.object(engine, 'process_file', side_effect=extract):
            app._set_busy(True)
            worker = threading.Thread(target=app._batch_worker,
                                      args=(list(app.sources.items()), Path(tmp)))
            worker.start()
            try:
                self.assertTrue(started.wait(3))
                ticks = []
                self.root.after(0, lambda: ticks.append(True))
                self.root.update()
                self.assertTrue(ticks)
                app._stop()
            finally:
                app.stop_event.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
            # Drain manually without accumulating duplicate after callbacks.
            self.root.after_cancel(app._drain_after)
            while not app.events.empty():
                app._drain()
                self.root.after_cancel(app._drain_after)
            self.assertFalse(app.busy)
            self.assertEqual(len(calls), 1)
            self.assertEqual([app.tasks.set(item, 'status') for item in app.sources], ['已停止', '已停止'])
            self.assertTrue(app.start_button.instate(['!disabled']))


if __name__ == '__main__':
    unittest.main()
