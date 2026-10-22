"""Native-first ownership and disconnected surface exports; no network calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from components import (classify_native_first, body_group_audit, build_candidates,
                        resolve_categories)
from core.source_regions import merge_surface_patterns
from reviewed_exports import LABELS, export_reviewed
from test_components import decision
from test_reviewed_exports import fixture
from tests.test_body_surface_audit import scene
from vision import recognize, VisionError


def prepare_batch(batch, folder, *args):
    folder.mkdir(parents=True, exist_ok=True)


class NativeFirstTests(unittest.TestCase):
    def test_structural_local_member_does_not_create_second_region_inside_body(self):
        _, candidates, first = scene()
        def classify(batch, path, config, **kwargs):
            allowed = {c['id'] for c in batch}
            return decision(车身=[gid for gid in ('G001', 'G002', 'G003') if gid in allowed])
        with tempfile.TemporaryDirectory() as temp, \
                patch('components.batch_inputs', side_effect=prepare_batch), \
                patch('components.recognize_groups', side_effect=classify):
            checked, _ = classify_native_first(candidates, Path(temp), {'component_batch_size': 12})
        self.assertEqual([r['anchor_ids'] for r in checked['source_regions']], [['G001'], ['G002']])

    def test_cross_stage_conflict_is_reviewed_without_silently_trimming_group(self):
        objects, candidates, first = scene()
        group = dict(candidates[0], id='G020', member_ids=['base', 'roof'],
                     members=objects[:2], native_type='AGroup')
        candidates.append(group)
        conflicts = []
        def classify(batch, path, config, **kwargs):
            allowed = {c['id'] for c in batch}
            if path.name == 'conflicts':
                conflicts.append(allowed)
                return decision(彩条=['G020'])
            if config['_native_group_stage']:
                return decision(车身=[gid for gid in ('G001', 'G002') if gid in allowed],
                                彩条=['G020'] if 'G020' in allowed else [])
            return decision(车漆=['G003'] if 'G003' in allowed else [])
        with tempfile.TemporaryDirectory() as temp, \
                patch('components.batch_inputs', side_effect=prepare_batch), \
                patch('components.recognize_groups', side_effect=classify):
            checked, _ = classify_native_first(candidates, Path(temp), {'component_batch_size': 12})
        self.assertEqual(conflicts, [{'G003', 'G020'}])
        self.assertEqual(set(resolve_categories(objects, candidates, checked)['彩条']), {'base', 'roof'})
        self.assertEqual(group['member_ids'], ['base', 'roof'])

    def test_all_native_batches_finish_before_local_members_with_confirmed_context(self):
        objects, candidates, first = scene()
        before = deepcopy(candidates)
        calls = []

        def classify(batch, path, config, **kwargs):
            native = config['_native_group_stage']
            calls.append((native, [c['id'] for c in batch]))
            if not native:
                self.assertEqual(set(config['_confirmed_body_ids']), {'G001', 'G002'})
                self.assertEqual({a['id'] for a in config['_context_anchors']}, {'G001', 'G002'})
            allowed = {c['id'] for c in batch}
            return decision(**{name: [gid for gid in ids if gid in allowed]
                               for name, ids in first['categories'].items()})

        with tempfile.TemporaryDirectory() as temp, \
                patch('components.batch_inputs', side_effect=prepare_batch), \
                patch('components.recognize_groups', side_effect=classify):
            result, coverage = classify_native_first(candidates, Path(temp),
                {'_region_workflow': True, 'component_batch_size': 1, '_all_candidates': candidates})
        self.assertEqual([native for native, _ in calls], [True, True, False, False])
        self.assertCountEqual([gid for _, batch in calls for gid in batch], [c['id'] for c in candidates])
        self.assertEqual(resolve_categories(objects, candidates, result),
                         resolve_categories(objects, candidates, first))
        self.assertEqual(coverage['initial_classification_batches'], 4)
        self.assertEqual(candidates, before)

    def test_unknown_local_id_fails_without_changing_native_members(self):
        _, candidates, _ = scene()
        before = deepcopy(candidates)

        def classify(batch, path, config, **kwargs):
            return decision(车身=[batch[0]['id']]) if config['_native_group_stage'] else decision(车漆=['G999'])

        with tempfile.TemporaryDirectory() as temp, \
                patch('components.batch_inputs', side_effect=prepare_batch), \
                patch('components.recognize_groups', side_effect=classify):
            with self.assertRaises(VisionError):
                classify_native_first(candidates, Path(temp), {'component_batch_size': 12})
        self.assertEqual(candidates, before)

    def test_body_conserves_every_member_except_explicit_paint_transfer(self):
        objects, candidates, first = scene()
        lamp = objects[3]
        candidates.append(dict(id='G005', native_type='PaintSurface', member_ids=['lamp'],
                               members=[lamp], bbox=lamp['bbox'], area=40))
        first['categories']['车漆'].append('G005')
        records = body_group_audit(objects, candidates, first)
        side = next(r for r in records if r['group_id'] == 'G001')
        self.assertEqual(set(side['transfers']['车漆']), {'base', 'lamp'})
        self.assertIn('glass', side['retained_member_ids'])
        self.assertIn('tyre', side['retained_member_ids'])
        self.assertEqual(set(side['original_member_ids']),
                         set(side['retained_member_ids']) | set(side['transfers']['车漆']))
        self.assertEqual(candidates[0]['member_ids'], side['original_member_ids'])

    def test_large_native_groups_are_not_lost_at_generated_candidate_budget(self):
        objects, groups = [], []
        for n in range(125):
            start = n*100
            groups.append(dict(class_name='AGroup', source_offset=start, end_offset=start+90))
            for i in range(2):
                objects.append(dict(id=f'{n}-{i}', source_offset=start+10+i,
                    bbox=[n*110, 0, n*110+100, 40], fill='#888888', d='M0 0H100V40Z'))
        candidates, _ = build_candidates(objects, groups, limit=120)
        native = [c for c in candidates if c['native_type'] == 'AGroup']
        self.assertEqual(len(native), 125)
        self.assertEqual(len({c['id'] for c in candidates}), len(candidates))
        self.assertTrue(all(len(c['member_ids']) == 2 for c in native))

    def test_native_stage_has_dedicated_prompt_for_both_protocols(self):
        for model in ('qwen-test', 'other-test'):
            payload = (dict(labels={'G001': '车身'}, reason='完整结构', confidence=.9)
                       if model.startswith('qwen') else decision(车身=['G001']))
            response = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {
                'content': json.dumps(payload)}}]}).encode()
            with self.subTest(model=model), patch('vision._image_part', return_value={'type': 'image_url'}), \
                    patch('vision._request_bytes', return_value=response) as send:
                recognized = recognize(dict(component_mode=True, native_group_stage=True,
                    objects=[dict(id='G001', bbox=[0, 0, 10, 5])], group_metadata=[]),
                    'source.png', 'atlas.png', dict(enabled=True, model=model,
                    base_url='https://example.invalid/v1', api_key='test'))
            prompt = json.loads(send.call_args.args[0].data)['messages'][1]['content'][0]['text']
            self.assertIn('原生整组识别阶段', prompt)
            self.assertIn('混合玻璃、灯具和漆面的组不能整体归车漆', prompt)
            self.assertIn('即使混在灯具组内也应归车漆', prompt)
            self.assertNotIn('CURRENT PAINT', prompt)
            self.assertEqual(recognized['categories']['车身'], ['G001'])


class SurfaceBundleTests(unittest.TestCase):
    def test_review_audit_reflects_final_human_transfers_and_unselected_members(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = fixture(root)
            result['body_group_audit'] = [dict(group_id='G001', native_type='AGroup',
                original_member_ids=['0', '1', '2'], retained_member_ids=['0', '1', '2'], transfers={})]
            choices = {name: None for name in LABELS}
            choices.update(车身=['0'], 车漆=['1'])
            published = export_reviewed(result, choices, root/'exports')
            audit = published['manifest']['body_group_audit'][0]
            self.assertEqual(audit['retained_member_ids'], ['0'])
            self.assertEqual(audit['transfers'], {'车漆': ['1']})
            self.assertEqual(audit['unselected_member_ids'], ['2'])
            self.assertEqual(result['body_group_audit'][0]['transfers'], {})

    def test_merge_only_same_region_and_direction_and_respect_explicit_split(self):
        entries = [dict(selected_ids=[str(i)], source_region=region, source_view=view,
                        keep_separate=separate) for i, (region, view, separate) in enumerate([
            ('R1', '左视图', False), ('R1', '左视图', False), ('R2', '左视图', False),
            ('R1', '右视图', False), ('R1', '左视图', True), (None, '左视图', False),
            (None, '左视图', False)])]
        before = deepcopy(entries)
        for name in ('车身', '车漆'):
            merged = merge_surface_patterns(name, entries)
            self.assertEqual(len(merged), 6)
            self.assertEqual(merged[0]['selected_ids'], ['0', '1'])
        self.assertEqual(merge_surface_patterns('彩条', entries), entries)
        self.assertEqual(entries, before)

    def test_three_disconnected_paint_parts_export_one_svg_in_source_order(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = fixture(root)
            result['source_regions'] = [dict(id='R1', name='侧面A', source_view='左视图',
                bbox=[0, 0, 120, 10], selected_ids=[])]
            choices = {name: None for name in LABELS}
            choices['车漆'] = ['0', '2', '4']
            patterns = {name: [] for name in LABELS}
            patterns['车漆'] = [dict(selected_ids=[oid], source_view='左视图') for oid in ('4', '0', '2')]
            before = deepcopy(patterns)
            published = export_reviewed(result, choices, root/'exports', patterns=patterns)
            record = published['manifest']['categories']['车漆']
            self.assertEqual(len(record['patterns']), 1)
            self.assertEqual(set(record['patterns'][0]['selected_ids']), {'0', '2', '4'})
            self.assertEqual(len(record['files']), 2)
            svg = (Path(published['output_dir'])/record['files'][0]).read_text(encoding='utf8')
            positions = [svg.index(o['d']) for o in result['objects'] if o['id'] in choices['车漆']]
            self.assertEqual(positions, sorted(positions))
            self.assertEqual(patterns, before)

    def test_manual_separate_flag_round_trips_through_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = fixture(root)
            result['source_regions'] = [dict(id='R1', name='侧面A', source_view='左视图',
                bbox=[0, 0, 120, 10], selected_ids=[])]
            choices = {name: None for name in LABELS}
            choices['车漆'] = ['0', '2']
            patterns = {name: [] for name in LABELS}
            patterns['车漆'] = [dict(selected_ids=[oid], source_view='左视图', keep_separate=True)
                               for oid in ('0', '2')]
            published = export_reviewed(result, choices, root/'exports', patterns=patterns)
            entries = published['manifest']['categories']['车漆']['patterns']
            self.assertEqual(len(entries), 2)
            self.assertTrue(all(e['keep_separate'] for e in entries))


if __name__ == '__main__':
    unittest.main()
