"""Unit tests for multi-thread batch concurrency in CLI and UI."""
from __future__ import annotations

import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import patch, MagicMock

import cli
import ui


class TestBatchConcurrency(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.sources = [self.root / f"sample_{i}.fs" for i in range(4)]
        for s in self.sources:
            s.write_bytes(b"dummy fs content")
        self.output = self.root / "output"
        self.output.mkdir(parents=True, exist_ok=True)

    def _simultaneous_probe(self, workers):
        """A barrier fails if the requested number of tasks cannot overlap."""
        barrier = threading.Barrier(workers, timeout=5)
        lock = threading.Lock()
        state = {'active': 0, 'peak': 0}

        def process(source, out_dir, options=None, progress=None, cancel_event=None):
            self.assertTrue(options['use_ai'])
            with lock:
                state['active'] += 1
                state['peak'] = max(state['peak'], state['active'])
            try:
                barrier.wait()
                return {'source': str(source), 'status': 'review', 'output_dir': str(out_dir),
                        'selected_ids': ['1'], 'method': 'test', 'elapsed_seconds': 0,
                        'objects': [{}], 'candidate_count': 1,
                        'result_svg': str(out_dir/'result.svg'), 'warnings': []}
            finally:
                with lock:
                    state['active'] -= 1

        return process, state

    def test_cli_ai_tasks_really_overlap_at_requested_concurrency(self):
        for workers in (2, 4):
            with self.subTest(workers=workers):
                process, state = self._simultaneous_probe(workers)
                with patch('sys.argv', ['cli.py', str(self.root), '--ai', '-j', str(workers),
                                        '--output', str(self.output)]), \
                     patch('cli.process_file', side_effect=process):
                    self.assertEqual(cli.main(), 0)
                self.assertEqual(state['peak'], workers)

    def test_ui_ai_tasks_really_overlap_at_requested_concurrency(self):
        class DummyApp:
            def __init__(self):
                self.events = queue.Queue()
                self.stop_event = threading.Event()

        for workers in (2, 4):
            with self.subTest(workers=workers):
                app = DummyApp()
                process, state = self._simultaneous_probe(workers)
                with patch('engine.process_file', side_effect=process):
                    ui.LiveryApp._batch_worker(app, list(enumerate(self.sources)), self.output,
                                              use_ai=True, concurrency=workers)
                events = list(app.events.queue)
                self.assertEqual(sum(e[0] == 'result' for e in events), 4)
                self.assertFalse(any(e[0] in ('failed', 'error') for e in events))
                self.assertEqual(state['peak'], workers)

    def test_cli_concurrency_flag(self):
        with patch("sys.argv", ["cli.py", str(self.sources[0]), "-j", "8"]):
            with patch("cli.process_file") as mock_proc:
                mock_proc.return_value = {
                    "source": str(self.sources[0]),
                    "status": "ready",
                    "output_dir": str(self.output),
                    "selected_ids": ["1", "2"],
                    "method": "test",
                    "elapsed_seconds": 0.1,
                    "objects": [{}, {}],
                    "candidate_count": 2,
                    "result_svg": "out.svg",
                }
                code = cli.main()
                self.assertEqual(code, 0)
                mock_proc.assert_called_once()

    def test_cli_parallel_batch_run(self):
        with patch("sys.argv", ["cli.py", str(self.root), "-j", "4", "--output", str(self.output)]):
            with patch("cli.process_file") as mock_proc:
                def fake_proc(source, out_dir, options=None, progress=None):
                    return {
                        "source": str(source),
                        "status": "ready",
                        "output_dir": str(out_dir),
                        "selected_ids": ["10"],
                        "method": "concurrent_test",
                        "elapsed_seconds": 0.05,
                        "objects": [{}],
                        "candidate_count": 1,
                        "result_svg": str(out_dir / "result.svg"),
                    }
                mock_proc.side_effect = fake_proc
                code = cli.main()
                self.assertEqual(code, 0)
                self.assertEqual(mock_proc.call_count, 4)
                # Check batch json created
                batch_files = list(self.output.glob("batch_*.json"))
                self.assertTrue(len(batch_files) >= 1)
                data = json.loads(batch_files[0].read_text(encoding="utf-8"))
                self.assertEqual(len(data), 4)

    def test_ui_batch_worker_concurrency(self):
        class DummyApp:
            def __init__(self):
                self.events = queue.Queue()
                self.stop_event = threading.Event()
                self.sources = {}

        dummy = DummyApp()
        jobs = [(f"item_{i}", s) for i, s in enumerate(self.sources)]
        dummy.sources = dict(jobs)

        with patch("engine.process_file") as mock_proc:
            mock_proc.return_value = {
                "source": "dummy",
                "status": "ready",
                "selected_ids": ["1"],
                "warnings": [],
            }
            # Instantiate bound method from ui.LiveryApp
            ui.LiveryApp._batch_worker(
                dummy,
                jobs=jobs,
                output=self.output,
                force=False,
                use_ai=False,
                concurrency=4,
            )

        events = []
        while not dummy.events.empty():
            events.append(dummy.events.get_nowait())

        running_events = [e for e in events if e[0] == "running"]
        result_events = [e for e in events if e[0] == "result"]
        step_events = [e for e in events if e[0] == "step"]
        done_events = [e for e in events if e[0] == "done"]

        self.assertEqual(len(running_events), 4)
        self.assertEqual(len(result_events), 4)
        self.assertEqual(len(step_events), 4)
        self.assertEqual(len(done_events), 1)


if __name__ == "__main__":
    unittest.main()
