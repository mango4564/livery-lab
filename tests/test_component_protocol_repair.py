"""Local protocol validation never asks a second model call to change a result."""
import json
from io import BytesIO
import unittest
from unittest.mock import patch

import components
import vision


def decision(**updates):
    categories = {name: [] for name in components.CATEGORIES}
    categories.update(updates)
    return {'categories': categories, 'reason': 'test', 'confidence': .8}


class ComponentProtocolRepairTests(unittest.TestCase):
    def setUp(self):
        self.candidates = [
            {'id': 'G1', 'bbox': [0, 0, 10, 10], 'native_type': 'AGroup',
             'source_span': [0, 10], 'member_ids': ['a', 'b'], 'area': 100, 'aspect': 1},
            {'id': 'G2', 'bbox': [0, 0, 5, 5], 'native_type': 'SourcePath',
             'source_span': [0, 5], 'member_ids': ['a'], 'area': 25, 'aspect': 1}]
        self.config = {'timeout_seconds': 90}

    def run_groups(self):
        return components.recognize_groups(self.candidates, 'unused', self.config)

    def test_invalid_response_is_rejected_without_model_correction(self):
        config = dict(self.config, enabled=True, base_url='https://offline.invalid/v1',
                      model='test', api_key='offline-key')
        raw = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {
            'content': '{}'}}]}).encode()
        with patch('vision._image_part', return_value={'type': 'image_url'}), \
                patch('vision._request_bytes', return_value=raw) as send:
            with self.assertRaises(vision.VisionError):
                components.recognize_groups(self.candidates, 'unused', config)
        self.assertEqual(send.call_count, 1)
        self.assertTrue(send.call_args.kwargs['strict_deadline'])

    def test_conflicting_proposals_remain_visible_for_manual_assignment(self):
        original = decision(车身=['G1'], 彩条=['G1'], 车漆=['G2'])
        with patch('vision.recognize', return_value=original) as model:
            result = self.run_groups()
        self.assertEqual(model.call_count, 1)
        self.assertEqual(result['categories']['彩条'], [])
        self.assertEqual(result['categories']['车漆'], [])
        self.assertEqual(original['categories']['彩条'], ['G1'])
        reviews = components.review_candidates(self.candidates, result)
        self.assertEqual(reviews[0]['conflict_categories'], ['彩条'])
        self.assertIn('彩条', reviews[0]['category_hints'])
        self.assertEqual(reviews[1]['conflict_categories'], ['车漆'])
        self.assertEqual(reviews[0]['selected_ids'], ['a', 'b'])
        self.assertEqual(reviews[1]['selected_ids'], ['a'])

    def test_transport_failure_does_not_trigger_semantic_retry(self):
        with patch('vision.recognize', side_effect=vision.VisionError('HTTP 401')) as model:
            with self.assertRaisesRegex(vision.VisionError, '401'):
                self.run_groups()
        self.assertEqual(model.call_count, 1)

    def test_allowed_body_and_emblem_sharing_stay_unchanged(self):
        for result in (decision(车身=['G1'], 车漆=['G2']), decision(门徽=['G1'], logo=['G2'])):
            with patch('vision.recognize', return_value=result) as model:
                self.assertEqual(self.run_groups(), result)
            self.assertEqual(model.call_count, 1)

    def test_stream_keepalives_cannot_extend_request_deadline(self):
        with patch('vision.time.monotonic', side_effect=[0, 3]), self.assertRaises(TimeoutError):
            vision._read_with_deadline(BytesIO(b'data: keepalive\n'), 2, 1024)
        with patch('vision.time.monotonic', return_value=0):
            self.assertEqual(vision._read_with_deadline(BytesIO(b'body'), 2, 1024), b'body')
            with self.assertRaises(vision.VisionError):
                vision._read_with_deadline(BytesIO(b'body too large'), 2, 3)


if __name__ == '__main__':
    unittest.main()
