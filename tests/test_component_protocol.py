"""Strict six-category JSON contracts and one bounded correction, without network."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from components import recognize_groups
from test_components import decision
from vision import ComponentProtocolError, VisionError, recognize


def envelope(payload):
    return json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(payload)}}]}).encode()


class ComponentProtocolTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        self.candidates = [{'id': gid, 'member_ids': [str(i)], 'source_span': [i, i+1],
                            'native_type': 'AGroup', 'bbox': [0, 0, 100, 30], 'score': 1}
                           for i, gid in enumerate(('G001', 'G002'))]
        self.config = {'enabled':True, 'base_url':'https://example.invalid/v1',
                       'model':'same-model', 'api_key':'test-key', 'timeout_seconds':90}

    def audit(self):
        return json.loads((self.directory/'classification_validation.json').read_text(encoding='utf8'))

    def request(self, replies):
        with patch('vision._image_part', return_value={'type':'image_url'}), \
                patch('vision._request_bytes', side_effect=[envelope(p) for p in replies]) as transport:
            result = recognize_groups(self.candidates, self.directory, self.config)
        return result, transport

    def test_body_only_top_level_shape_is_corrected_with_full_schema(self):
        invalid = {'车身':['G001']}
        valid = decision(车身=['G001'])
        result, transport = self.request([invalid, valid])
        self.assertEqual(result, valid)
        self.assertEqual(transport.call_count, 2)
        prompt = json.loads(transport.call_args.args[0].data)['messages'][1]['content'][0]['text']
        self.assertIn('不得省略 categories 包装、reason 或 confidence', prompt)
        self.assertIn('received_json', prompt)
        self.assertIn('"车漆": []', prompt)
        self.assertEqual([v['status'] for v in self.audit()], ['protocol_error', 'validated'])

    def test_unknown_source_view_id_is_repaired_not_silently_removed(self):
        invalid = dict(decision(文字=['G002']), source_views={'G999':'左视图'})
        valid = dict(decision(文字=['G002']), source_views={'G002':'待确认视图'})
        result, transport = self.request([invalid, valid])
        self.assertEqual(result, valid)
        self.assertEqual(transport.call_count, 2)
        self.assertIn('来源视图', self.audit()[0]['error'])

    def test_inverse_direction_groups_are_losslessly_converted_without_retry(self):
        inverse = dict(decision(车身=['G001']), source_views={'左视图':['G001'], '右视图':['G002']})
        before = deepcopy(inverse)
        result, transport = self.request([inverse])
        self.assertEqual(result['source_views'], {'G001':'左视图','G002':'右视图'})
        self.assertEqual(inverse, before)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(self.audit()[0]['protocol_normalizations'], ['source_views_direction_groups_to_id_map'])

    def test_ambiguous_or_unknown_inverse_views_are_not_converted(self):
        from vision import _canonical_component_payload
        for views in ({'左视图':['G999']}, {'侧面A':['G001']},
                      {'左视图':['G001'],'右视图':['G001']}, {'左视图':'G001'}):
            payload = dict(decision(), source_views=views)
            converted, notes = _canonical_component_payload(payload, {'G001','G002'})
            self.assertEqual(converted, payload)
            self.assertEqual(notes, [])

    def test_invalid_direction_is_not_accepted_without_correction(self):
        invalid = dict(decision(), source_views={'G001':'侧面A'})
        with self.assertRaisesRegex(ValueError, '纠正一次后仍不符合返回协议'):
            self.request([invalid, invalid])
        self.assertEqual(len(self.audit()), 2)

    def test_unknown_selection_stays_rejected_after_bounded_retry(self):
        with self.assertRaisesRegex(ValueError, '未知或未提供'):
            self.request([decision(车身=['G999']), decision(车身=['G999'])])
        self.assertEqual(len(self.audit()), 2)

    def test_schema_repair_does_not_erase_valid_body_selection(self):
        invalid = dict(decision(车身=['G001']), source_views={'G999':'左视图'})
        with self.assertRaisesRegex(ValueError, '丢失原有无冲突分类.*车身'):
            self.request([invalid, decision()])
        self.assertEqual(self.audit()[-1]['status'], 'dropped_valid_selection')

    def test_single_pass_metadata_matches_baseline_fields(self):
        self.candidates[0].update(body_parent_ids=['G999'], body_child_ids=['G002'])
        before = deepcopy(self.candidates)
        with patch('vision.recognize', return_value=decision()) as model:
            recognize_groups(self.candidates, self.directory, self.config)
        scene = model.call_args.args[0]
        self.assertNotIn('stage_context', scene)
        self.assertEqual(set(scene['group_metadata'][0]),
                         {'id', 'bbox', 'native_type', 'source_span', 'path_count'})
        self.assertEqual(self.candidates, before)

    def test_protocol_and_ownership_repairs_share_one_retry_limit(self):
        conflict = decision(文字=['G001'], 彩条=['G001'])
        with self.assertRaisesRegex(ValueError, '纠正一次后仍有分类冲突'):
            self.request([{'车身':[]}, conflict])
        self.assertEqual([v['status'] for v in self.audit()], ['protocol_error','source_overlap'])

    def test_protocol_correction_uses_remaining_original_budget(self):
        with patch('vision.recognize', side_effect=[ComponentProtocolError({}, '缺少categories'), decision()]) as model, \
                patch('components.time.monotonic', side_effect=[0, 0, 40]):
            recognize_groups(self.candidates, self.directory, self.config)
        self.assertEqual([c.args[3]['timeout_seconds'] for c in model.call_args_list], [90, 50])

    def test_malformed_json_is_not_mistaken_for_a_schema_repair(self):
        malformed = json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{broken'}}]}).encode()
        with patch('vision._image_part', return_value={'type':'image_url'}), \
                patch('vision._request_bytes', return_value=malformed) as transport:
            with self.assertRaises(VisionError):
                recognize_groups(self.candidates, self.directory, self.config)
        self.assertEqual(transport.call_count, 1)


if __name__ == '__main__':
    unittest.main()
