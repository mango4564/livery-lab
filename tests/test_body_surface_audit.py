"""Residual paint review preserves structure and uses explicit model labels."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from components import (residual_surface_candidates, refine_body_surfaces,
                        resolve_categories, run)
from test_components import decision
from vision import recognize


def scene():
    objects = []
    for index, (name, box, fill) in enumerate((
            ('base', [0, 0, 100, 50], '#ffffff'),
            ('roof', [0, 49, 50, 51], '#cc0000'),
            ('glass', [10, 20, 70, 40], '#ffffff'),
            ('lamp', [95, 5, 99, 15], '#cc0000'),
            ('tyre', [15, 0, 25, 10], '#000000'),
            ('rear-blue', [200, 5, 203, 15], '#004488'),
            ('rear-yellow', [203, 5, 207, 15], '#ffaa00'),
            ('stripe', [207, 10, 209, 15], '#555555'),
            ('caption', [201, 5, 207, 8], '#ffffff'))):
        objects.append(dict(id=name, source_offset=index*100, bbox=box, fill=fill,
                            details={'closed': True}, d='M0 0H5V5H0Z'))
    def candidate(gid, ids, box, kind='AGroup'):
        return dict(id=gid, member_ids=ids, members=[o for o in objects if o['id'] in ids],
                    native_type=kind, bbox=box, area=(box[2]-box[0])*(box[3]-box[1]),
                    aspect=(box[2]-box[0])/(box[3]-box[1]))
    candidates = [candidate('G001', [o['id'] for o in objects[:5]], [0, 0, 100, 55]),
                  candidate('G002', [o['id'] for o in objects[5:]], [200, 0, 210, 20]),
                  candidate('G003', ['base'], [0, 0, 100, 50], 'PaintSurface'),
                  candidate('G004', ['caption'], [201, 5, 207, 8], 'AText')]
    return objects, candidates, decision(车身=['G001', 'G002'], 车漆=['G003'])


class BodySurfaceAuditTests(unittest.TestCase):
    def test_omitted_sibling_motifs_are_reviewed_per_vehicle_without_claimed_logos(self):
        objects, candidates, first = scene()
        for index, (name, box) in enumerate((
                ('palm-left', [70, 10, 90, 30]),
                ('curve-left', [20, 10, 90, 15]),
                ('palm-right', [201, 10, 206, 17]),
                ('outside-logo', [110, 10, 130, 30]),
                ('claimed-logo', [40, 10, 50, 20]))):
            objects.append(dict(id=name, bbox=box, source_offset=1000+index,
                                fill='#325537', d='M0 0H5V5H0Z'))
        logo = objects[-1]
        candidates.append(dict(id='G005', native_type='EmblemLogo',
                               members=[logo], member_ids=[logo['id']], bbox=logo['bbox'],
                               area=100, aspect=1))
        first['categories']['logo'] = ['G005']
        audit, _ = residual_surface_candidates(objects, candidates, first, limit=4)
        proposals = {c['member_ids'][0]: c for c in audit}
        self.assertTrue({'palm-left', 'curve-left', 'palm-right'} <= proposals.keys())
        self.assertEqual(proposals['palm-left']['body_anchor_id'], 'G001')
        self.assertEqual(proposals['palm-right']['body_anchor_id'], 'G002')
        self.assertNotIn('outside-logo', proposals)
        self.assertNotIn('claimed-logo', proposals)
        self.assertEqual(resolve_categories(objects, candidates, first)['彩条'], [])

    def test_local_scale_keeps_small_multicolour_end_faces_without_auto_deletion(self):
        objects, candidates, first = scene()
        before = deepcopy(first)
        audit, total = residual_surface_candidates(objects, candidates, first)
        proposed = {c['member_ids'][0] for c in audit}
        self.assertEqual(proposed, {'roof', 'glass', 'lamp', 'tyre',
                                    'rear-blue', 'rear-yellow', 'stripe'})
        self.assertEqual(total, 7)
        self.assertEqual(first, before)
        self.assertTrue(all(c['native_type']=='PaintSurface' for c in audit))
        self.assertTrue(all('category_hints' not in c for c in audit))
        self.assertTrue(set(c['id'] for c in audit).isdisjoint(c['id'] for c in candidates))

    def test_budget_reserves_both_regions_and_reports_omissions(self):
        objects, candidates, first = scene()
        audit, total = residual_surface_candidates(objects, candidates, first, limit=2)
        self.assertEqual(total, 7)
        self.assertEqual({c['body_anchor_id'] for c in audit}, {'G001', 'G002'})
        self.assertEqual(len(audit), 2)
        empty, total = residual_surface_candidates(objects, candidates, decision())
        self.assertEqual((empty, total), ([], 0))

    def test_only_confirmed_paint_and_stripes_are_subtracted_structure_stays(self):
        objects, candidates, first = scene()
        before = deepcopy(first)
        def classify(audit, directory, config, **kwargs):
            self.assertTrue(kwargs['surface_audit'])
            ids = {c['member_ids'][0]: c['id'] for c in audit}
            return decision(车漆=[ids['roof']],
                            彩条=[ids[n] for n in ('rear-blue', 'rear-yellow', 'stripe')])
        with tempfile.TemporaryDirectory() as directory:
            with patch('components.recognize_groups', side_effect=classify) as model, \
                    patch('components.rasterize'), patch('components.candidate_atlas'):
                merged, coverage = refine_body_surfaces(objects, candidates, first, directory, {})
            model.assert_called_once()
            self.assertTrue((Path(directory)/'body_surface_audit/decision.json').exists())
        resolved = resolve_categories(objects, candidates, merged)
        self.assertEqual(set(resolved['车漆']), {'base', 'roof'})
        self.assertEqual(set(resolved['彩条']), {'rear-blue', 'rear-yellow', 'stripe'})
        self.assertEqual(set(resolved['车身']), {'glass', 'lamp', 'tyre', 'caption'})
        self.assertEqual(coverage['body_surface_audit_classified'], 4)
        self.assertEqual(first, before)

    def test_no_body_does_not_call_model(self):
        objects, candidates, first = scene()
        first['categories']['车身'] = []
        with tempfile.TemporaryDirectory() as directory, patch('components.recognize_groups') as model:
            merged, coverage = refine_body_surfaces(objects, candidates, first, directory, {})
        model.assert_not_called()
        self.assertEqual(merged, first)
        self.assertEqual(coverage['body_surface_audit_candidates'], 0)

    def test_normal_extraction_runs_audit_but_manual_mode_never_calls_model(self):
        objects, candidates, first = scene()
        def classify(proposals, directory, config, **kwargs):
            if not kwargs.get('surface_audit'):
                return first
            return decision(车漆=[c['id'] for c in proposals if c['member_ids'] in (['roof'], ['base'])])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory);source=path/'source.fs';source.write_bytes(b'fixture')
            with patch('components.prepare', return_value=(objects, deepcopy(candidates), {})), \
                    patch('components.recognize_groups', side_effect=classify) as model, \
                    patch('components.rasterize'), patch('components.candidate_atlas'), \
                    patch('components.batch_inputs', side_effect=lambda batch, folder, *args: folder.mkdir(parents=True, exist_ok=True)):
                _, report = run(source, path, {'max_candidates': 120})
            self.assertEqual(model.call_count, 4)  # Initial, two regional audits, same-fill lamp recheck.
            self.assertNotIn('roof', report['categories']['车身'])
            self.assertIn('roof', report['categories']['车漆'])
            self.assertEqual(report['coverage']['body_surface_audit_classified'], 1)
            with patch('components.prepare', return_value=(objects, deepcopy(candidates), {})), \
                    patch('components.recognize_groups') as model, patch('components.rasterize'):
                run(source, path, {'max_candidates': 120}, use_ai=False)
            model.assert_not_called()

    def test_audit_request_explains_residual_preview_and_rejects_structural_parts(self):
        payload = decision()
        response = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps(payload)}}]}).encode()
        proposal = dict(id='G121', bbox=[0, 0, 20, 1], score=1)
        with patch('vision._image_part', return_value={'type':'image_url'}) as images, \
                patch('vision._request_bytes', return_value=response) as send:
            recognize(dict(objects=[proposal], component_mode=True, body_surface_audit=True,
                           group_metadata=[], candidate_sheet_png='residual.png'),
                      'source.png', 'atlas.png', dict(enabled=True, base_url='https://example.invalid/v1',
                                                    model='vision', api_key='test'))
        prompt = json.loads(send.call_args.args[0].data)['messages'][1]['content'][0]['text']
        self.assertIn('车身残留面复核', prompt)
        self.assertIn('第三张是扣除首轮', prompt)
        self.assertIn('玻璃、窗框、轮胎、灯具及其高光', prompt)
        self.assertIn('同一区域内首轮漏分类', prompt)
        self.assertIn('椰树', prompt)
        self.assertIn('逐个左右/前后区域独立核对', prompt)
        self.assertIn('成套蓝黄装饰块应归彩条', prompt)
        self.assertIn('镜壳的蓝色基础漆面仍归车漆', prompt)
        self.assertEqual(images.call_count, 3)

    def test_colour_and_matching_paint_surfaces_precede_large_neutral_structure(self):
        objects = [
            dict(id='body-shell', source_offset=0, bbox=[0, 0, 100, 100], fill='#ffffff',
                 details={'closed': True}, d='M0 0H100V100H0Z'),
            dict(id='confirmed-stripe', source_offset=10, bbox=[10, 10, 30, 20], fill='#ffc056',
                 details={'closed': True}, d='M10 10H30V20H10Z'),
            dict(id='huge-glass', source_offset=20, bbox=[10, 20, 90, 80], fill='#ffffff',
                 details={'closed': True}, d='M10 20H90V80H10Z'),
            dict(id='huge-chassis', source_offset=30, bbox=[10, 80, 90, 95], fill='#000000',
                 details={'closed': True}, d='M10 80H90V95H10Z'),
            dict(id='matching-corner', source_offset=40, bbox=[80, 85, 95, 95], fill='#ffc056',
                 details={'closed': True}, d='M80 85H95V95H80Z'),
            dict(id='other-color-piece', source_offset=50, bbox=[20, 85, 35, 95], fill='#004488',
                 details={'closed': True}, d='M20 85H35V95H20Z'),
        ]
        candidates = [
            dict(id='G001', native_type='AGroup', member_ids=[o['id'] for o in objects],
                 members=list(objects), bbox=[0, 0, 100, 100], area=10000, aspect=1.0),
            dict(id='G002', native_type='PaintSurface', member_ids=['confirmed-stripe'],
                 members=[objects[1]], bbox=objects[1]['bbox'], area=200, aspect=2.0,
                 source_span=[10, 10]),
        ]
        first = decision(车身=['G001'], 彩条=['G002'])
        audit, total = residual_surface_candidates(objects, candidates, first, limit=2)
        proposed_ids = [c['member_ids'][0] for c in audit]
        self.assertEqual(proposed_ids, ['matching-corner', 'other-color-piece'])
        self.assertEqual(total, 5)

    def test_first_pass_distinguishes_decorative_rear_blocks_from_base_paint(self):
        response = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps(decision())}}]}).encode()
        with patch('vision._image_part', return_value={'type': 'image_url'}), \
                patch('vision._request_bytes', return_value=response) as send:
            recognize(dict(objects=[dict(id='G001', bbox=[0, 0, 20, 1], score=1)],
                           component_mode=True, group_metadata=[]),
                      'source.png', 'atlas.png', dict(enabled=True, base_url='https://example.invalid/v1',
                                                    model='vision', api_key='test'))
        prompt = json.loads(send.call_args.args[0].data)['messages'][1]['content'][0]['text']
        self.assertIn('成套蓝黄装饰块应归彩条', prompt)
        self.assertIn('不按是否采用喷漆工艺区分', prompt)
        self.assertIn('覆盖保险杠或与底色同色', prompt)


if __name__ == '__main__':
    unittest.main()


