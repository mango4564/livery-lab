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
