"""All-empty atlas recovery uses explicit labels from bounded same-model calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from components import recover_empty_classification
from test_components import decision
from vision import VisionError, recognize


class EmptyRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        (self.path/'source_scene.png').write_bytes(b'preview')
        self.candidates = [{'id': f'G{i:03d}', 'member_ids': [f'p{i}']} for i in range(1, 46)]
        self.config = {'model': 'same-provider'}

    def recover(self, classifier, initial=None):
        with patch('components.candidate_atlas') as atlas, \
             patch('components.detail_candidates', return_value=[]), \
             patch('components.recognize_groups', side_effect=classifier) as recognize:
            result = recover_empty_classification(self.candidates, initial or decision(),
                                                   self.path, self.config)
        return result, atlas, recognize

    def test_bounded_batches_keep_ids_and_original_decision(self):
        first = decision()
        before = deepcopy(first)
        def classify(batch, path, config, **kwargs):
            self.assertIs(config, self.config)
            self.assertTrue(kwargs['classification_batch'])
            self.assertEqual((path/'source_scene.png').read_bytes(), b'preview')
            return decision(彩条=[batch[-1]['id']])
        (recovered, coverage), atlas, recognize = self.recover(classify, first)
        self.assertEqual([len(c.args[0]) for c in recognize.call_args_list], [20, 20, 5])
        self.assertEqual(recovered['categories']['彩条'], ['G020', 'G040', 'G045'])
        self.assertEqual(coverage['empty_recovery_classified'], 3)
        self.assertEqual(first, before)
        self.assertEqual(atlas.call_count, 3)

    def test_nonempty_first_pass_never_calls_provider(self):
        (recovered, coverage), atlas, recognize = self.recover(None, decision(车身=['G001']))
        recognize.assert_not_called()
        atlas.assert_not_called()
        self.assertEqual(coverage['empty_recovery_batches'], 0)

    def test_repeated_empty_result_does_not_invent_labels_or_loop(self):
        (recovered, coverage), _, recognize = self.recover(lambda *args, **kwargs: decision())
        self.assertFalse(any(recovered['categories'].values()))
        self.assertEqual(recognize.call_count, 3)
        self.assertEqual(coverage['empty_recovery_classified'], 0)

    def test_response_cannot_select_id_from_another_batch(self):
        with self.assertRaises(VisionError):
            self.recover(lambda *args, **kwargs: decision(彩条=['G045']))

    def test_region_ids_names_and_directions_merge_without_collisions(self):
        def classify(batch, *args, **kwargs):
            checked = decision(车身=[batch[0]['id']])
            checked.update(source_regions=[dict(id='R1', name='侧'*40,
                source_view='待确认视图', anchor_ids=[batch[0]['id']])],
                source_views={batch[0]['id']: '待确认视图'})
            return checked
        (recovered, _), _, _ = self.recover(classify)
        regions = recovered['source_regions']
        self.assertEqual([r['id'] for r in regions], ['R1', 'R2', 'R3'])
        self.assertEqual(len({r['name'] for r in regions}), 3)
        self.assertTrue(all(len(r['name']) <= 40 for r in regions))
        self.assertEqual(set(recovered['source_views']), {'G001', 'G021', 'G041'})

    def test_transport_failure_is_not_swallowed_as_empty_success(self):
        with self.assertRaisesRegex(VisionError, 'offline'):
            self.recover(lambda *args, **kwargs: (_ for _ in ()).throw(VisionError('offline')))

    def test_body_regions_use_only_explicit_labels_and_unknown_direction_stays_unknown(self):
        (recovered, _), _, _ = self.recover(
            lambda batch, *args, **kwargs: decision(车身=[batch[0]['id']]))
        self.assertEqual([r['anchor_ids'] for r in recovered['source_regions']],
                         [['G001'], ['G021'], ['G041']])
        self.assertTrue(all(r['source_view']=='待确认视图' for r in recovered['source_regions']))

    def test_batch_prompt_limits_ids_and_does_not_request_global_region_anchors(self):
        response = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps(decision())}}]}).encode()
        with patch('vision._image_part', return_value={'type': 'image_url'}), \
             patch('vision._request_bytes', return_value=response) as send:
            recognize(dict(objects=[dict(id='G118', bbox=[0, 0, 10, 2])],
                           component_mode=True, classification_batch=True, group_metadata=[]),
                      'source.png', 'atlas.png', dict(enabled=True, base_url='https://example.invalid/v1',
                                                    model='vision', api_key='test'))
        prompt = json.loads(send.call_args.args[0].data)['messages'][1]['content'][0]['text']
        self.assertIn('source_regions必须为空数组', prompt)
        self.assertIn('本批允许的全部编号：["G118"]', prompt)

    def test_overlapping_source_groups_get_one_joint_review(self):
        self.candidates[20]['member_ids'] = ['p1', 'p21']
        def classify(batch, path, config, **kwargs):
            if path.name == '01':
                return decision(车漆=['G001'])
            if path.name == '02':
                return decision(彩条=['G021'])
            if path.name == 'conflicts':
                self.assertEqual([c['id'] for c in batch], ['G001', 'G021'])
                return decision(彩条=['G021'])
            return decision()
        (recovered, coverage), _, recognize = self.recover(classify)
        self.assertEqual(recognize.call_count, 4)
        self.assertEqual(recovered['categories']['彩条'], ['G021'])
        self.assertEqual(recovered['categories']['车漆'], [])
        self.assertEqual(coverage['empty_recovery_conflict_groups'], 2)
        self.assertEqual(coverage['empty_recovery_classified'], 1)

    def test_unresolved_source_conflicts_are_left_for_manual_review(self):
        self.candidates[20]['member_ids'] = ['p1', 'p21']
        def classify(batch, path, config, **kwargs):
            if path.name == '01':
                return decision(车漆=['G001'])
            if path.name == '02':
                return decision(彩条=['G021'])
            if path.name == 'conflicts':
                return decision(车漆=['G001'], 彩条=['G021'])
            return decision(彩条=['G045'])
        (recovered, coverage), _, recognize = self.recover(classify)
        self.assertEqual(recognize.call_count, 4)
        self.assertEqual(coverage['empty_recovery_unresolved_groups'], 2)
        self.assertEqual(recovered['categories']['彩条'], ['G045'])
        self.assertEqual(recovered['categories']['车漆'], [])

    def test_empty_conflict_review_also_reports_manual_groups(self):
        self.candidates[20]['member_ids'] = ['p1', 'p21']
        def classify(batch, path, config, **kwargs):
            if path.name == '01':
                return decision(车漆=['G001'])
            if path.name == '02':
                return decision(彩条=['G021'])
            return decision()
        (recovered, coverage), _, _ = self.recover(classify)
        self.assertEqual(coverage['empty_recovery_unresolved_groups'], 2)
        self.assertFalse(any(recovered['categories'].values()))


if __name__ == '__main__':
    unittest.main()
