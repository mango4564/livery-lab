"""Source proposal coverage for small logos and decorations on every view."""
import json
import unittest
from unittest.mock import patch

from components import build_candidates, detail_candidates, review_candidates, CATEGORIES
from vision import recognize


class RegionGraphicCoverageTests(unittest.TestCase):
    def test_graphic_priority_does_not_displace_complete_end_view_anchors(self):
        objects, containers = [], []
        for index in range(12):
            offset = index*100
            containers.append(dict(class_name='AGroup', source_offset=offset, end_offset=offset+90))
            for j, color in enumerate(('#008040', '#000080')):
                objects.append(dict(id=f'mark-{index}-{j}', source_offset=offset+10+j*20,
                                    bbox=[index*10, 0, index*10+2, 2], fill=color, d='M0 0H1V1Z'))
        ends = []
        for index in range(2):
            offset = 2000+index*2000
            containers.append(dict(class_name='AGroup', source_offset=offset, end_offset=offset+1500))
            ids = []
            for j in range(110):
                oid = f'end-{index}-{j}';ids.append(oid)
                objects.append(dict(id=oid, source_offset=offset+10+j*10,
                                    bbox=[200+index*100, 0, 250+index*100, 200],
                                    fill='none', d='M0 0H1V1Z'))
            ends.append(set(ids))
        candidates, _ = build_candidates(objects, containers, limit=18)
        supplied = [set(c['member_ids']) for c in candidates]
        self.assertTrue(all(ids in supplied for ids in ends))
        self.assertLessEqual(len(candidates), 18)

    def test_unlettered_and_gradient_marks_at_every_placement_survive_area_caps(self):
        objects, containers = [], []
        for index in range(65):
            offset = index*100
            containers.append(dict(class_name='AGroup', source_offset=offset, end_offset=offset+90))
            for j in range(2):
                objects.append(dict(id=f'large-{index}-{j}', source_offset=offset+10+j*20,
                                    bbox=[100, 100, 200, 150], fill='#777777', d='M0 0H1V1Z'))
        expected = []
        for index, size in enumerate((2, 2, 12, 40)):
            offset = 7000+index*100
            containers.append(dict(class_name='AGroup', source_offset=offset, end_offset=offset+90))
            ids = [f'mark-{index}-{j}' for j in range(2)];expected.append(set(ids))
            objects.extend([
                dict(id=ids[0], source_offset=offset+10, bbox=[0, index*30, size, index*30+size],
                     fill='#f1811d', d='M0 0H1V1Z'),
                dict(id=ids[1], source_offset=offset+30, bbox=[0, index*30, size, index*30+size],
                     fill=None, gradient={'definition': {'stops': [{'color': '#3d74a4'}]}},
                     d='M0 0H1V1Z')])
        candidates, _ = build_candidates(objects, containers)
        supplied = [set(c['member_ids']) for c in candidates if c['native_type']=='DetailGraphic']
        self.assertTrue(all(ids in supplied for ids in expected))
        self.assertLessEqual(len(candidates), 120)
        self.assertTrue(all('category_hints' not in c for c in candidates))

    def test_neutral_compact_graphic_is_review_evidence_without_logo_hint(self):
        objects = [dict(id='neutral-'+str(i), source_offset=10+i*10,
                        bbox=[0, 0, 5, 5], fill='#777777',
                        d='M0 0C1 0 2 0 3 1C4 2 4 3 3 4C2 5 1 5 0 0Z') for i in range(3)]
        objects.extend(dict(id='body-'+str(i), source_offset=110+i*10,
                            bbox=[100, 100, 300, 300], fill='#777777', d='M0 0H1V1Z')
                       for i in range(2))
        containers = [dict(class_name='AGroup', source_offset=0, end_offset=50),
                      dict(class_name='AGroup', source_offset=100, end_offset=150)]
        candidates, _ = build_candidates(objects, containers)
        neutral = next(c for c in candidates if c['native_type']=='CompactGraphic')
        self.assertEqual(set(neutral['member_ids']), {'neutral-0', 'neutral-1', 'neutral-2'})
        self.assertIn(neutral, detail_candidates(candidates))
        records = review_candidates(candidates, {'categories': {n: [] for n in CATEGORIES}})
        self.assertNotIn('logo', next(c for c in records if c['id']==neutral['id'])['category_hints'])

    def test_native_multicolour_stripes_on_both_sides_enter_detail_evidence(self):
        candidates = []
        for i, y in enumerate((0, 100)):
            candidates.append(dict(id=f'G{i}', native_type='AGroup', bbox=[0, y, 300, y+60],
                                   members=[{'fill': color} for color in
                                            ('#9d6f3e', '#da6522', '#ffffff')]))
        candidates.extend([
            dict(id='body', native_type='AGroup', bbox=[0, 0, 500, 180],
                 members=[{'fill': '#ffffff'}]*200),
            dict(id='frame', native_type='AGroup', bbox=[0, 0, 300, 60],
                 members=[{'fill': 'none'}]*4),
            dict(id='mark', native_type='DetailGraphic', bbox=[1, 1, 3, 3],
                 members=[{'fill': '#008080'}])])
        detail = detail_candidates(candidates)
        self.assertEqual([c['id'] for c in detail], ['G0', 'G1', 'mark'])
        self.assertIs(detail[0], candidates[0])  # IDs and membership stay unchanged.
        self.assertTrue(all('category_hints' not in c for c in detail))

    def test_straight_front_rear_and_side_end_pieces_survive_candidate_cap(self):
        objects = []
        for side, y in enumerate((0, 100)):
            for i in range(14):
                objects.append(dict(id=f'side-{side}-{i}', source_offset=len(objects)*10,
                                    bbox=[0, y, 100, y+20], fill='#167ce2',
                                    d=f'M0 {y} C10 {y+1} 20 {y+5} 100 {y+20} Z'))
            objects.append(dict(id=f'end-{side}', source_offset=len(objects)*10,
                                bbox=[99, y+15, 105, y+21], fill='#167ce2',
                                d=f'M99 {y+15} L105 {y+15} L105 {y+21} L99 {y+21} Z'))
        for view, y in (('front', 0), ('rear', 100)):
            objects.append(dict(id=view, source_offset=len(objects)*10,
                                bbox=[140, y, 180, y+5], fill='#167ce2',
                                d=f'M140 {y} L180 {y} L180 {y+5} Z'))
        candidates, _ = build_candidates(objects, [], limit=120)
        supplied = {i for c in candidates for i in c['member_ids']}
        self.assertTrue({'front', 'rear', 'end-0', 'end-1'} <= supplied)
        for side in (0, 1):
            bundle = next(c for c in candidates if c['native_type']=='SpatialPaths'
                          and f'end-{side}' in c['member_ids'])
            self.assertTrue(all(f'side-{side}-{i}' in bundle['member_ids'] for i in range(14)))
            self.assertNotIn('front', bundle['member_ids'])
            self.assertNotIn('rear', bundle['member_ids'])
        self.assertLessEqual(len(candidates), 120)

    def test_patch_in_empty_curve_bounds_is_not_a_stripe_companion(self):
        objects = [
            dict(id='stripe', source_offset=0, bbox=[0, 0, 100, 34], fill='#167ce2',
                 d='M0 0H50L80 30H100V34H78L48 4H0Z'),
            dict(id='unrelated-patch', source_offset=10, bbox=[20, 24, 26, 30], fill='#167ce2',
                 d='M20 24H26V30H20Z'),
            dict(id='attached-end', source_offset=20, bbox=[100, 30, 105, 34], fill='#167ce2',
                 d='M100 30H105V34H100Z'),
            dict(id='scene-extent', source_offset=30, bbox=[0, 100, 105, 120], fill='none',
                 d='M0 100H105V120H0Z')]
        candidates, _ = build_candidates(objects, [], limit=120)
        bundle = next(c for c in candidates if c['native_type']=='SpatialPaths')
        self.assertEqual(set(bundle['member_ids']), {'stripe', 'attached-end'})
        self.assertNotIn('unrelated-patch', {i for c in candidates
                                           if c['native_type']=='SpatialPaths' for i in c['member_ids']})

    def test_small_converted_multicolour_mark_survives_large_native_groups(self):
        objects, containers = [], []
        for i in range(70):
            offset = i*100
            containers.append(dict(class_name='AGroup', source_offset=offset, end_offset=offset+90))
            for j in range(2):
                objects.append(dict(id=f'part-{i}-{j}', source_offset=offset+10+j*20,
                                    bbox=[100, 100+i, 200, 150+i], fill='#777777', d='M0 0L1 1Z'))
        containers.append(dict(class_name='AGroup', source_offset=8000, end_offset=8100))
        objects.extend([dict(id='mark', source_offset=8010, bbox=[10, 10, 12, 12],
                             fill='#008040', d='M10 10L12 10L12 12Z'),
                        dict(id='converted-caption', source_offset=8040, bbox=[10, 9, 16, 10],
                             fill='#000080', d=' '.join('M10 9L11 9L11 10Z' for _ in range(20)))])
        candidates, _ = build_candidates(objects, containers, limit=120)
        mark = next(c for c in candidates if set(c['member_ids'])=={'mark', 'converted-caption'})
        self.assertEqual(mark['native_type'], 'DetailGraphic')
        review = review_candidates(candidates, {'categories': {name: [] for name in CATEGORIES}})
        self.assertNotIn('logo', next(c for c in review if c['id']==mark['id'])['category_hints'])
        self.assertLessEqual(len(candidates), 120)

    def test_component_request_requires_all_regions_and_includes_detail_atlas(self):
        categories = {name: [] for name in CATEGORIES}
        payload = dict(categories=categories, reason='test', confidence=.9)
        response = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {
            'content': json.dumps(payload, ensure_ascii=False)}}]}).encode()
        scene = dict(objects=[dict(id='G1', bbox=[0, 0, 10, 10], score=1)],
                     component_mode=True, group_metadata=[], candidate_sheet_png='details.png')
        config = dict(enabled=True, base_url='https://example.invalid/v1', model='vision', api_key='test')
        with patch('vision._image_part', return_value={'type': 'image_url'}) as images, \
                patch('vision._request_bytes', return_value=response) as send:
            recognize(scene, 'source.png', 'groups.png', config)
        content = json.loads(send.call_args.args[0].data)['messages'][1]['content']
        self.assertEqual(images.call_count, 3)
        self.assertIn('逐个原稿区域', content[0]['text'])
        self.assertIn('不能只选一侧', content[0]['text'])
        self.assertIn('同一区域内可能同时有多个不同logo或门徽', content[0]['text'])
        self.assertIn('完整弧形公司名仍归门徽', content[0]['text'])
        self.assertNotIn('优先选择上方同一侧', content[0]['text'])


if __name__ == '__main__':
    unittest.main()
