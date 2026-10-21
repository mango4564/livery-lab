"""First-pass pagination, local conflict isolation and source directions."""
from copy import deepcopy
import json
import tempfile
import unittest
from unittest.mock import patch

import components
from tests.test_component_protocol_repair import decision
from tests import test_component_protocol_repair as protocol_tests
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
from vision import VisionError


class InternalModelReviewTests(unittest.TestCase):
    def page_fixture(self):
        objects = [dict(id=f'o{i}', source_offset=i, bbox=[i*2, 0, i*2+1, 1],
                        fill='#ffffff', d=f'M{i*2} 0h1v1h-1z') for i in range(25)]
        candidates = [dict(id=f'G{i}', native_type='PaintSurface', members=[o],
            member_ids=[o['id']], bbox=o['bbox'], source_span=[i, i], area=1, aspect=1)
            for i, o in enumerate(objects)]
        candidates[-1]['coverage_anchor'] = True
        return objects, candidates

    def test_initial_pages_prioritize_unselected_surface_and_keep_deadline(self):
        objects, candidates = self.page_fixture()
        def classify(page, directory, config, **kwargs):
            return decision(车漆=[page[0]['id']])
        with tempfile.TemporaryDirectory() as directory, patch('components.candidate_atlas'), \
                patch('components.rasterize'), patch('components.recognize_groups', side_effect=classify) as model:
            merged = components.recognize_initial_groups(objects, candidates, directory, {'timeout_seconds': 90})
        self.assertEqual([len(c.args[0]) for c in model.call_args_list], [24, 1])
        self.assertEqual(model.call_args_list[0].args[0][0]['id'], 'G24')
        self.assertTrue(all(0 < c.args[2]['timeout_seconds'] <= 90 for c in model.call_args_list))
        self.assertIn('G24', merged['categories']['车漆'])

    def test_initial_later_page_failure_preserves_valid_selection(self):
        objects, candidates = self.page_fixture()
        with tempfile.TemporaryDirectory() as directory, patch('components.candidate_atlas'), \
                patch('components.rasterize'), patch('components.recognize_groups',
                    side_effect=[decision(车漆=['G24']), VisionError('network')]):
            merged = components.recognize_initial_groups(objects, candidates, directory, {})
        self.assertEqual(merged['categories']['车漆'], ['G24'])
        self.assertEqual(merged['classification_unresolved'][0]['kind'], 'classification_page_failed')

    def test_initial_cross_page_source_conflict_is_isolated(self):
        objects, candidates = self.page_fixture()
        candidates[-2]['member_ids'] = candidates[-1]['member_ids'][:]
        with tempfile.TemporaryDirectory() as directory, patch('components.candidate_atlas'), \
                patch('components.rasterize'), patch('components.recognize_groups',
                    side_effect=[decision(彩条=['G24']), decision(车漆=['G23'])]):
            merged = components.recognize_initial_groups(objects, candidates, directory, {})
        self.assertEqual(merged['categories']['车漆'], [])
        self.assertEqual(merged['categories']['彩条'], [])
        self.assertTrue(merged['classification_unresolved'])


    def test_part_direction_inherits_unique_vehicle_region(self):
        objects, candidates, first = scene()
        candidates.append(dict(id='G005', native_type='PaintSurface', member_ids=['rear-blue'],
            members=[objects[5]], bbox=objects[5]['bbox']))
        first['source_views'] = {'G005': '右视图'}
        first['source_regions'] = [dict(id='R1', name='前视图', source_view='前视图', anchor_ids=['G002'])]
        before = deepcopy(first['categories'])
        components.inherit_source_views(objects, candidates, first)
        self.assertEqual(first['source_views']['G005'], '前视图')
        self.assertEqual(first['categories'], before)

    def test_ambiguous_region_does_not_invent_part_direction(self):
        objects, candidates, first = scene()
        first['source_views'] = {'G003': '待确认视图'}
        first['source_regions'] = [
            dict(id='R1', name='区域A', source_view='左视图', anchor_ids=['G001']),
            dict(id='R2', name='区域B', source_view='右视图', anchor_ids=['G001'])]
        components.inherit_source_views(objects, candidates, first)
        self.assertEqual(first['source_views']['G003'], '待确认视图')

    def test_later_full_region_replaces_contained_anchor_without_duplicates(self):
        objects, candidates = self.page_fixture()
        candidates[-2]['bbox'] = [-1, -1, 50, 2]
        one, two = decision(车身=['G0']), decision(车身=['G23'])
        for value, gid in ((one, 'G0'), (two, 'G23')):
            value['source_regions'] = [dict(id='R1', name='左视图', source_view='左视图', anchor_ids=[gid])]
        with tempfile.TemporaryDirectory() as directory, patch('components.candidate_atlas'), \
                patch('components.rasterize'), patch('components.recognize_groups', side_effect=[one, two]):
            merged = components.recognize_initial_groups(objects, candidates, directory, {})
        self.assertEqual(len(merged['source_regions']), 1)
        self.assertEqual(merged['source_regions'][0]['anchor_ids'], ['G23'])

    def test_persistent_source_conflict_defers_both_proposals_and_keeps_body(self):
        fixture = protocol_tests.ComponentProtocolRepairTests()
        fixture.setUp()
        bad = decision(车身=['G1'], 彩条=['G1'], 车漆=['G2'])
        before = deepcopy(bad)
        with patch('vision.recognize', return_value=bad) as model:
            result = components.recognize_groups(fixture.candidates, 'unused',
                fixture.config)
        self.assertEqual(model.call_count, 1)
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


if __name__ == '__main__':
    unittest.main()
