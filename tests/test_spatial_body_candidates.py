"""Ungrouped end views remain available without inventing source geometry."""
import unittest

from components import (CATEGORIES, build_candidates, resolve_categories,
                        review_candidates, spatial_body_candidates)


def rect(oid, offset, box, fill='none'):
    x, y, r, b = box
    return dict(id=oid, source_offset=offset, bbox=box, fill=fill,
                d=f'M{x} {y}H{r}V{b}H{x}', details={'closed': False})


def scene():
    side = [rect('side-outline', 10, [0, 0, 300, 100])]
    side += [rect(f'side-{i}', 20+i, [i, 10, i+1, 20]) for i in range(101)]
    front = [rect('front-outline', 210, [320, 0, 390, 100])]
    front += [rect(f'front-{i}', 220+i, [325+i, 10, 326+i, 20]) for i in range(24)]
    # Glass and white base must survive proposal construction, alongside lines.
    front += [rect('glass', 250, [325, 5, 385, 50], '#aabbcc'),
              rect('paint', 251, [325, 50, 385, 90], '#ffffff')]
    front[-1]['details']['closed'] = True
    front[-1]['d'] += 'Z'
    return side+front, [dict(class_name='AGroup', source_offset=0, end_offset=200)]


class SpatialBodyTests(unittest.TestCase):
    def test_initial_pool_and_default_body_review_include_all_source_members(self):
        objects, containers = scene()
        candidates, coverage = build_candidates(objects, containers, 120, include_omitted=True)
        proposals = [c for c in candidates if c['native_type'] == 'SpatialBody']
        self.assertEqual(coverage['spatial_body_candidates'], 1)
        self.assertEqual(len(proposals), 1)
        proposal = proposals[0]
        expected = [o for o in objects if o['id'].startswith('front') or o['id'] in ('glass', 'paint')]
        self.assertEqual(proposal['members'], expected)
        self.assertTrue(all(a is b for a, b in zip(proposal['members'], expected)))
        self.assertTrue(proposal['initial_candidate'])
        empty = {'categories': {name: [] for name in CATEGORIES}}
        review = next(c for c in review_candidates(candidates, empty) if c['id'] == proposal['id'])
        self.assertEqual(review['category_hints'], ['车身'])
        self.assertEqual(review['recommended_categories'], [])
        empty['categories']['车身'] = [proposal['id']]
        paint = next(c for c in candidates if c['member_ids'] == ['paint'])
        empty['categories']['车漆'] = [paint['id']]
        resolved = resolve_categories(objects, candidates, empty)
        self.assertEqual(set(resolved['车身']), {o['id'] for o in expected}-{'paint'})
        self.assertEqual(resolved['车漆'], ['paint'])

    def test_neighbouring_views_and_detached_captions_do_not_merge(self):
        objects, containers = scene()
        front = [o for o in objects if o['source_offset'] >= 210]
        second = [rect('second-'+o['id'], o['source_offset']+100,
                       [o['bbox'][0], o['bbox'][1]+110, o['bbox'][2], o['bbox'][3]+110])
                  for o in front]
        objects += second+[rect('caption', 500, [320, 101, 390, 105])]
        cs, _ = build_candidates(objects, containers)
        proposals = [c for c in cs if c['native_type'] == 'SpatialBody']
        self.assertEqual(len(proposals), 2)
        self.assertTrue(set(proposals[0]['member_ids']).isdisjoint(proposals[1]['member_ids']))
        self.assertTrue(all('caption' not in c['member_ids'] for c in proposals))

    def test_existing_complete_native_end_view_is_not_duplicated(self):
        objects, containers = scene()
        objects += [rect(f'front-detail-{i}', 260+i, [330, 30, 331, 31]) for i in range(101)]
        containers += [dict(class_name='AGroup', source_offset=205, end_offset=400)]
        cs, coverage = build_candidates(objects, containers)
        self.assertEqual(coverage['spatial_body_candidates'], 0)
        self.assertFalse(any(c['native_type'] == 'SpatialBody' for c in cs))

    def test_small_marks_and_single_paint_panels_are_not_body_proposals(self):
        objects, containers = scene()
        objects = [o for o in objects if o['source_offset'] < 200]
        objects += [rect('small-'+str(i), 220+i, [320, 0, 330, 15]) for i in range(24)]
        objects += [rect('panel', 300, [350, 0, 420, 100], '#ffffff')]
        _, coverage = build_candidates(objects, containers)
        self.assertEqual(coverage['spatial_body_candidates'], 0)

    def test_no_side_scale_does_not_guess_vehicle_semantics(self):
        objects, _ = scene()
        self.assertEqual(spatial_body_candidates(objects, []), [])


if __name__ == '__main__':
    unittest.main()
