"""Offline protocol repair and source ownership boundaries; no paid requests."""
import json
import unittest
from copy import deepcopy
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
             'source_span': [0, 10], 'member_ids': ['a', 'b']},
            {'id': 'G2', 'bbox': [0, 0, 5, 5], 'native_type': 'SourcePath',
             'source_span': [0, 5], 'member_ids': ['a']}]
        self.config = {'timeout_seconds': 90}

    def run_groups(self):
        return components.recognize_groups(self.candidates, 'unused', self.config)

    def test_schema_failure_gets_one_correction(self):
        scenes = []
        good = decision(彩条=['G1'])

        def reply(scene, *args):
            scenes.append(deepcopy(scene))
            if len(scenes) == 1:
                raise vision.VisionProtocolError('缺少必填字段')
            return good

        with patch('vision.recognize', side_effect=reply) as model:
            self.assertEqual(self.run_groups(), good)
        self.assertEqual(model.call_count, 2)
        self.assertNotIn('_protocol_feedback', scenes[0])
        self.assertIn('缺少必填字段', scenes[1]['_protocol_feedback'])

    def test_different_group_ids_sharing_source_are_corrected_before_return(self):
        bad = decision(彩条=['G1'], 车漆=['G2'])
        good = decision(彩条=['G1'])
        scenes = []

        def reply(scene, *args):
            scenes.append(deepcopy(scene))
            return bad if len(scenes) == 1 else good

        with patch('vision.recognize', side_effect=reply):
            self.assertEqual(self.run_groups(), good)
        self.assertIn('["彩条", "G1", "车漆", "G2"]', scenes[1]['_protocol_feedback'])
        self.assertEqual(bad['categories']['车漆'], ['G2'])

    def test_persistent_conflict_is_rejected_after_two_calls(self):
        with patch('vision.recognize', return_value=decision(彩条=['G1'], 车漆=['G2'])) as model:
            with self.assertRaisesRegex(vision.VisionError, '仍冲突'):
                self.run_groups()
        self.assertEqual(model.call_count, 2)

    def test_transport_failure_is_not_replayed_as_protocol_repair(self):
        with patch('vision.recognize', side_effect=vision.VisionError('HTTP 401')) as model:
            with self.assertRaisesRegex(vision.VisionError, '401'):
                self.run_groups()
        self.assertEqual(model.call_count, 1)

    def test_allowed_body_and_emblem_sharing_do_not_trigger_repair(self):
        for result in (decision(车身=['G1'], 车漆=['G2']),
                       decision(门徽=['G1'], logo=['G2'])):
            with patch('vision.recognize', return_value=result) as model:
                self.assertEqual(self.run_groups(), result)
            self.assertEqual(model.call_count, 1)

    def test_no_correction_when_total_budget_exhausted(self):
        with patch('components.time.monotonic', side_effect=[0, 0, 91]), \
             patch('vision.recognize', side_effect=vision.VisionProtocolError('invalid')) as model:
            with self.assertRaisesRegex(vision.VisionError, '总超时'):
                self.run_groups()
        self.assertEqual(model.call_count, 1)

    def test_real_response_validation_drives_repair_and_request_deadline(self):
        good = decision(彩条=['G1'])
        def envelope(payload):
            return json.dumps({'choices': [{'finish_reason': 'stop', 'message': {
                'content': json.dumps(payload)}}]}).encode()
        config = dict(self.config, enabled=True, base_url='https://offline.invalid/v1',
                      model='test', api_key='offline-key')
        with patch('vision._image_part', return_value={'type': 'image_url'}), \
             patch('vision._request_bytes', side_effect=[envelope({}), envelope(good)]) as send:
            result = components.recognize_groups(self.candidates, 'unused', config)
        self.assertEqual(result, good)
        self.assertEqual(send.call_count, 2)
        for call in send.call_args_list:
            self.assertTrue(call.kwargs['strict_deadline'])
        body = json.loads(send.call_args_list[1].args[0].data)
        prompt = body['messages'][1]['content'][0]['text']
        self.assertIn('上次输出未通过本地校验', prompt)
        self.assertIn('输出结构示例', prompt)


if __name__ == '__main__':
    unittest.main()
