"""Bounded protocol recovery and exact-input checkpoints; no paid API calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import components
import vision
from tests import test_ai_request_diagnostics as diagnostics


class BatchResumeTests(unittest.TestCase):
    call = diagnostics.RequestDiagnosticsTests.call

    def setUp(self):
        diagnostics.RequestDiagnosticsTests.setUp(self)
        self.config.update(_batch_cache_root=str(self.path/'cache'), _batch_cache_namespace='source:pipeline')
        self.opener.open.return_value = self.response

    def test_success_survives_later_failure_and_new_run(self):
        self.call()
        self.scene['objects'][0]['id'] = 'G002'
        with self.assertRaises(vision.CompactLabelError):
            self.call()
        self.scene['objects'][0]['id'] = 'G001'
        self.scene['request_trace_dir'] = str(self.path/'next_run'/'ai_requests')
        self.call()
        self.assertEqual(self.opener.open.call_count, 2)
        record = vision.request_summary(self.path/'next_run'/'ai_requests')['requests'][0]
        self.assertTrue(record['cache_hit'])
        self.assertEqual(record['attempts'], [])

    def test_config_and_image_changes_invalidate(self):
        self.call()
        self.config['temperature'] = 0.2
        self.call()
        self.image.write_bytes(self.image.read_bytes()+b'changed')
        self.call()
        self.assertEqual(self.opener.open.call_count, 3)

    def test_corrupted_checkpoint_is_not_reused(self):
        self.call()
        checkpoint = next((self.path/'cache').glob('*.json'))
        saved = json.loads(checkpoint.read_text(encoding='utf8'))
        saved['result']['categories']['彩条'] = []
        checkpoint.write_text(json.dumps(saved), encoding='utf8')
        self.call()
        self.assertEqual(self.opener.open.call_count, 2)

    def test_corrected_response_reuses_original_request_identity(self):
        self.config['_protocol_retry'] = 1
        self.call()
        self.config.pop('_protocol_retry')
        self.call()
        self.assertEqual(self.opener.open.call_count, 1)

    def test_source_or_pipeline_change_does_not_reuse(self):
        self.call()
        self.config['_batch_cache_namespace'] = 'other_source:pipeline'
        self.call()
        self.assertEqual(self.opener.open.call_count, 2)


class SingletonRecoveryTests(unittest.TestCase):
    def test_second_correction_can_succeed(self):
        candidate = dict(id='G001', bbox=[0, 0, 2, 2], native_type='SourcePath', source_span=[0, 1], member_ids=['x'])
        result = {'categories': {name: [] for name in components.CATEGORIES}, 'reason': 'ok', 'confidence': 0.9}
        result['categories']['彩条'] = ['G001']
        replies = [vision.CompactLabelError('bad'), vision.CompactLabelError('bad'), result]
        with tempfile.TemporaryDirectory() as folder, patch.object(vision, 'recognize', side_effect=replies) as call:
            actual = components.recognize_groups([candidate], Path(folder), {}, classification_batch=True)
            self.assertEqual(actual['categories']['彩条'], ['G001'])
            self.assertEqual(call.call_count, 3)

    def test_retry_is_bounded_and_correction_is_requested(self):
        candidate = dict(id='G001', bbox=[0, 0, 2, 2], native_type='SourcePath', source_span=[0, 1], member_ids=['x'])
        with tempfile.TemporaryDirectory() as folder, patch.object(vision, 'recognize', side_effect=vision.CompactLabelError('bad')) as call:
            with self.assertRaises(vision.CompactLabelError):
                components.recognize_groups([candidate], Path(folder), {}, classification_batch=True)
            self.assertEqual(call.call_count, 3)
            self.assertEqual(call.call_args.args[3]['_protocol_retry'], 2)

    def test_transport_failure_does_not_become_format_retry(self):
        candidate = dict(id='G001', bbox=[0, 0, 2, 2], native_type='SourcePath', source_span=[0, 1], member_ids=['x'])
        with tempfile.TemporaryDirectory() as folder, patch.object(vision, 'recognize', side_effect=vision.VisionError('transport')) as call:
            with self.assertRaises(vision.VisionError):
                components.recognize_groups([candidate], Path(folder), {}, classification_batch=True)
            self.assertEqual(call.call_count, 1)
