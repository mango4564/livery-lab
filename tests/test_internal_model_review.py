"""Small-model recovery: local evidence, strict ownership and review status."""
from copy import deepcopy
import tempfile
import unittest
from unittest.mock import patch

import components
from tests.test_component_protocol_repair import decision
from tests import test_component_protocol_repair as protocol_tests
from tests.test_body_surface_audit import scene
from vision import VisionError


class InternalModelReviewTests(unittest.TestCase):
    def test_persistent_source_conflict_defers_both_proposals_and_keeps_body(self):
        fixture = protocol_tests.ComponentProtocolRepairTests()
        fixture.setUp()
        bad = decision(车身=['G1'], 彩条=['G1'], 车漆=['G2'])
        before = deepcopy(bad)
        with patch('vision.recognize', return_value=bad) as model:
            result = components.recognize_groups(fixture.candidates, 'unused',
                dict(fixture.config, _review_on_conflict=True))
        self.assertEqual(model.call_count, 2)
        self.assertEqual(bad, before)
        self.assertEqual(result['categories']['车身'], ['G1'])
        self.assertEqual(result['categories']['彩条'], [])
        self.assertEqual(result['categories']['车漆'], [])
        self.assertEqual(len(result['classification_unresolved'][0]['candidates']), 2)
        self.assertEqual(components.resolve_categories([{'id': 'a'}, {'id': 'b'}],
            fixture.candidates, result)['车身'], ['a', 'b'])

    def test_first_request_describes_shared_source_ids(self):
        fixture = protocol_tests.ComponentProtocolRepairTests()
        fixture.setUp()
        with patch('vision.recognize', return_value=decision()) as model:
            fixture.run_groups()
        self.assertEqual(model.call_args.args[0]['source_overlaps'], [['G1', 'G2', 1]])

    def test_unselected_front_surface_still_enters_residual_review(self):
        objects, candidates, first = scene()
        front = dict(id='front-base', bbox=[300, 0, 320, 30], fill='#c0b69d',
                     source_offset=5000, d='M300 0H320V30H300Z')
        objects.append(front)
        candidates.append(dict(id='G005', native_type='PaintSurface', coverage_anchor=True,
            member_ids=['front-base'], members=[front], bbox=front['bbox'], area=600, aspect=2/3))
        audit, _ = components.residual_surface_candidates(objects, candidates, first)
        self.assertTrue(any(c['member_ids']==['front-base'] for c in audit))
        self.assertEqual(first['categories']['车漆'], ['G003'])

    def test_cross_stage_conflict_rolls_back_page_instead_of_aborting_file(self):
        objects, candidates, first = scene()
        first['categories']['彩条'] = ['G002']
        # A broader later proposal may include already-classified stripes.
        proposal = dict(id='G100', native_type='PaintSurface', member_ids=['stripe'],
            members=[objects[7]], bbox=objects[7]['bbox'], body_anchor_id='G002')
        before = deepcopy(first)
        with tempfile.TemporaryDirectory() as directory, \
                patch('components.residual_surface_candidates', return_value=([proposal], 1)), \
                patch('components.candidate_atlas'), patch('components.rasterize'), \
                patch('components.recognize_groups', return_value=decision(车漆=['G100'])):
            merged, coverage = components.refine_body_surfaces(objects, candidates, first, directory, {})
        self.assertEqual(merged['categories'], before['categories'])
        self.assertEqual(coverage['body_surface_audit_classified'], 0)
        self.assertEqual(merged['classification_unresolved'][0]['kind'], 'surface_audit_incomplete')

    def test_large_residual_queue_is_local_and_bounded(self):
        objects, candidates, first = scene()
        proposals = [dict(id=f'G{100+i}', native_type='PaintSurface', member_ids=['roof'],
            members=[objects[1]], bbox=objects[1]['bbox'], body_anchor_id='G001') for i in range(25)]
        with tempfile.TemporaryDirectory() as directory, \
                patch('components.residual_surface_candidates', return_value=(proposals, 25)), \
                patch('components.candidate_atlas'), patch('components.rasterize'), \
                patch('components.recognize_groups', side_effect=[decision(), VisionError('timeout')]) as model:
            merged, coverage = components.refine_body_surfaces(objects, candidates, first, directory, {})
        self.assertEqual([len(c.args[0]) for c in model.call_args_list], [24, 1])
        self.assertEqual(coverage['body_surface_audit_checked_pages'], 1)
        self.assertEqual(merged['categories'], first['categories'])
        self.assertTrue(merged['classification_unresolved'])

    def test_incomplete_recognition_cannot_receive_passed_gate(self):
        objects, candidates, first = scene()
        first['classification_unresolved'] = [dict(kind='source_conflict')]
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'fixture.fs'
            source.write_bytes(b'offline')
            with patch('components.prepare', return_value=(objects, candidates, {})), \
                    patch('components.recognize_groups', return_value=first), \
                    patch('components.refine_body_surfaces', return_value=(first, {})), \
                    patch('core.candidate_audit.audit_components', return_value=(first, dict(status='passed'))), \
                    patch('components.rasterize'):
                _, report = components.run(source, directory, {'max_candidates': 120})
        self.assertEqual(report['quality_gate']['status'], 'review')
        self.assertEqual(report['quality_gate']['semantic_status'], 'review')

    def test_batch_rotation_checks_front_before_second_side_page(self):
        objects, candidates, first = scene()
        proposals = [dict(id=f'G{100+i}', native_type='PaintSurface', member_ids=['roof'],
            members=[objects[1]], bbox=objects[1]['bbox'], body_anchor_id='G001') for i in range(30)]
        proposals.append(dict(id='G200', native_type='PaintSurface', member_ids=['rear-blue'],
            members=[objects[5]], bbox=objects[5]['bbox'], body_anchor_id='G002'))
        with tempfile.TemporaryDirectory() as directory, \
                patch('components.residual_surface_candidates', return_value=(proposals, 31)), \
                patch('components.candidate_atlas'), patch('components.rasterize'), \
                patch('components.recognize_groups', return_value=decision()) as model:
            components.refine_body_surfaces(objects, candidates, first, directory, {})
        self.assertEqual([c.args[0][0]['body_anchor_id'] for c in model.call_args_list],
                         ['G001', 'G002', 'G001'])


if __name__ == '__main__':
    unittest.main()
