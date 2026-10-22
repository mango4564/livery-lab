"""Bounded same-model repair for candidate and expanded-source ownership."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from components import recognize_groups
from test_components import decision
from vision import ComponentClassificationError, VisionError, recognize as request_recognition


class ComponentConflictRepairTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.candidates = [
            {'id': gid, 'member_ids': members, 'native_type': 'AGroup', 'bbox': [0, 0, 10, 10],
             'source_span': [0, 100]}
            for gid, members in [('parent', ['paint', 'stripe']), ('child', ['stripe']), ('clean', ['paint'])]]
        self.config = {'model': 'same-model', 'timeout_seconds': 90}
        self.invalid = decision(车漆=['parent'], 彩条=['child'])
        self.valid = decision(车漆=['clean'], 彩条=['child'])

    def audit(self):
        return json.loads((self.directory/'classification_validation.json').read_text(encoding='utf8'))

    def test_different_groups_sharing_sources_receive_precise_feedback(self):
        observed = []
        def recognize(scene, preview, atlas, config):
            observed.append((deepcopy(scene), dict(config)))
            return self.invalid if len(observed)==1 else self.valid
        before = deepcopy(self.candidates)
        with patch('vision.recognize', side_effect=recognize):
            result = recognize_groups(self.candidates, self.directory, self.config)
        self.assertEqual(result, self.valid)
        self.assertEqual(len(observed), 2)
        feedback = observed[1][0]['component_conflict_feedback']
        conflict = feedback['source_conflicts'][0]
        self.assertEqual(conflict['source_count'], 1)
        self.assertEqual(conflict['categories'], ['车漆', '彩条'])
        self.assertEqual(conflict['candidate_pairs'][0]['first'], 'parent')
        self.assertTrue(conflict['candidate_pairs'][0]['first_contains_second'])
        self.assertEqual(observed[1][1]['model'], 'same-model')
        self.assertLessEqual(observed[1][1]['timeout_seconds'], observed[0][1]['timeout_seconds'])
        self.assertEqual([a['status'] for a in self.audit()], ['source_overlap', 'validated'])
        self.assertEqual(self.candidates, before)

    def test_unresolved_conflict_is_rejected_after_only_one_correction(self):
        with patch('vision.recognize', return_value=self.invalid) as recognize:
            with self.assertRaisesRegex(ValueError, '纠正一次.*车漆/彩条'):
                recognize_groups(self.candidates, self.directory, self.config)
        self.assertEqual(recognize.call_count, 2)
        self.assertEqual(len(self.audit()), 2)

    def test_clearing_valid_categories_is_not_accepted_as_a_repair(self):
        self.candidates.append(dict(self.candidates[0], id='unrelated', member_ids=['mark']))
        invalid = decision(车漆=['parent'], 彩条=['child'], logo=['unrelated'])
        with patch('vision.recognize', side_effect=[invalid, decision()]):
            with self.assertRaisesRegex(ValueError, '丢失原有无冲突分类.*logo'):
                recognize_groups(self.candidates, self.directory, self.config)
        self.assertEqual(self.audit()[-1]['status'], 'dropped_valid_selection')

    def test_duplicate_candidate_protocol_error_is_also_corrected(self):
        invalid = decision(车漆=['child'], 彩条=['child'])
        with patch('vision.recognize', side_effect=[ComponentClassificationError(invalid), self.valid]) as recognize:
            self.assertEqual(recognize_groups(self.candidates, self.directory, self.config), self.valid)
        self.assertEqual(recognize.call_count, 2)
        self.assertEqual(self.audit()[0]['conflicts'][0]['source_count'], 1)

    def test_allowed_logo_emblem_sharing_and_text_refinement_do_not_retry(self):
        valid = decision(logo=['child'], 门徽=['child'], 文字=['parent'])
        with patch('vision.recognize', return_value=valid) as recognize:
            self.assertEqual(recognize_groups(self.candidates, self.directory, self.config), valid)
        self.assertEqual(recognize.call_count, 1)
        self.assertEqual(self.audit()[0]['status'], 'validated')

    def test_network_or_invalid_json_errors_are_not_classification_retries(self):
        with patch('vision.recognize', side_effect=VisionError('invalid JSON')) as recognize:
            with self.assertRaises(VisionError):
                recognize_groups(self.candidates, self.directory, self.config)
        self.assertEqual(recognize.call_count, 1)

    def test_repair_shares_original_total_request_budget(self):
        with patch('vision.recognize', return_value=self.invalid) as recognize, \
                patch('components.time.monotonic', side_effect=[0, 0, 90]):
            with self.assertRaisesRegex(ValueError, '时间预算'):
                recognize_groups(self.candidates, self.directory, self.config)
        self.assertEqual(recognize.call_count, 1)

    def test_corrective_request_keeps_images_model_and_strict_schema_instructions(self):
        config = dict(self.config, enabled=True, base_url='https://example.invalid/v1', api_key='test-key')
        response = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(self.valid)}}]}
        scene = {'objects': self.candidates, 'component_mode': True,
                 'component_conflict_feedback': {'previous_categories': self.invalid['categories'],
                                                 'source_conflicts': []}}
        with patch('vision._image_part', return_value={'type': 'image_url'}), \
                patch('vision._request_bytes', return_value=json.dumps(response).encode()) as request:
            self.assertEqual(request_recognition(scene, 'unused', 'unused', config), self.valid)
        body = json.loads(request.call_args.args[0].data)
        self.assertEqual(body['model'], 'same-model')
        content = body['messages'][1]['content']
        self.assertEqual([entry['type'] for entry in content], ['text', 'image_url', 'image_url'])
        self.assertIn('不得返回previous_categories', content[0]['text'])
        self.assertNotIn('test-key', content[0]['text'])


if __name__ == '__main__':
    unittest.main()
