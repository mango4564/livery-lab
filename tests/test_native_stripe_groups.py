"""Complete native decoration groups survive partial seeds and vehicle bounds."""
import unittest

from components import (attach_native_contexts, residual_surface_candidates,
                        build_candidates, detail_candidates)
from tests.test_body_surface_audit import scene


class NativeStripeGroupTests(unittest.TestCase):
    def test_same_hue_native_motif_and_small_front_survive_busy_page(self):
        objects, containers = [], []

        def group(name, count, box, colors):
            start = len(containers)*10000
            containers.append(dict(class_name='AGroup', source_offset=start,
                                   end_offset=start+9000))
            ids = []
            for i in range(count):
                oid = f'{name}-{i}'; ids.append(oid)
                objects.append(dict(id=oid, source_offset=start+10+i*10,
                                    bbox=list(box), fill=colors[i % len(colors)],
                                    d='M0 0H1V1Z'))
            return set(ids)

        group('side', 500, [0, 0, 500, 150], ['#777777'])
        # Many colourful generated detail proposals used to displace these
        # single-hue/white groups, despite the native ownership being intact.
        for i in range(35):
            group(f'mark-{i}', 3, [i*3, 0, i*3+5, 5], ['#ff0000', '#0000ff'])
        motifs = [group(f'motif-{i}', 58, [i*200, 200, i*200+125, 280],
                        ['#ffaa00', '#ffffff', 'none']) for i in range(4)]
        front = group('front', 78, [600, 0, 700, 130], ['#777777'])
        candidates, _ = build_candidates(objects, containers)
        native = [set(c['member_ids']) for c in candidates if c['native_type']=='AGroup']
        self.assertTrue(all(ids in native for ids in motifs+[front]))
        details = [set(c['member_ids']) for c in detail_candidates(candidates)]
        self.assertTrue(all(ids in details for ids in motifs))
        self.assertLessEqual(len(candidates), 120)

    def test_equal_bounds_child_cannot_replace_parent_with_extra_outlines(self):
        objects = [dict(id=str(i), source_offset=20+i*10, bbox=[0, 0, 100, 50],
                        fill='#777777', d='M0 0H1V1Z') for i in range(78)]
        containers = [dict(class_name='AGroup', source_offset=0, end_offset=1000),
                      dict(class_name='AGroup', source_offset=15, end_offset=770)]
        candidates, _ = build_candidates(objects, containers)
        native = [c for c in candidates if c['native_type']=='AGroup']
        self.assertEqual(len(native), 1)
        self.assertEqual(native[0]['member_ids'], [o['id'] for o in objects])
        self.assertEqual(native[0]['source_span'], [0, 1000])

    def fixture(self):
        objects, candidates, first = scene()
        # A selected narrow line fits the vehicle, while the wider colour in
        # the same original group deliberately extends beyond its shell.
        seed = dict(id='seed', bbox=[10, 10, 90, 14], fill='#005599',
                    source_offset=1500, d='M10 10H90V14H10Z')
        wide = dict(id='wide', bbox=[20, 10, 106, 30], fill='#66ccee',
                    source_offset=1600, d='M20 10H106V30H20Z')
        objects.extend([seed, wide])
        group = dict(native_type='AGroup', member_ids=['seed', 'wide'],
                     members=[seed, wide], bbox=[10, 10, 106, 30],
                     area=1920, aspect=4.8, source_span=[1400, 1700])
        proposal = dict(id='G005', native_type='PaintSurface', member_ids=['seed'],
                        members=[seed], bbox=seed['bbox'], area=320, aspect=20,
                        source_span=[1500, 1500])
        candidates.append(proposal)
        first['categories']['彩条'] = ['G005']
        return objects, candidates, first, group

    def test_complete_group_is_an_atomic_review_proposal_not_automatic_expansion(self):
        objects, candidates, first, native = self.fixture()
        attach_native_contexts(candidates, [native])
        audit, _ = residual_surface_candidates(objects, candidates, first, limit=2)
        group = next(c for c in audit if c['native_type']=='NativeDecorationGroup')
        self.assertEqual(group['member_ids'], ['seed', 'wide'])
        self.assertEqual(group['source_span'], [1400, 1700])
        self.assertIs(group['members'][1], objects[-1])
        self.assertEqual(first['categories']['彩条'], ['G005'])
        self.assertEqual(candidates[-1]['member_ids'], ['seed'])

    def test_smallest_native_owner_is_used_and_whole_seed_is_not_rewrapped(self):
        objects, candidates, first, native = self.fixture()
        larger = dict(native, member_ids=['seed', 'wide', 'lamp'])
        attach_native_contexts(candidates, [larger, native])
        self.assertEqual(candidates[-1]['native_context']['member_ids'], ['seed', 'wide'])
        complete = dict(native)
        attach_native_contexts([complete], [native])
        self.assertNotIn('native_context', complete)

    def test_other_category_or_cross_view_group_cannot_expand_stripe(self):
        for protected in (True, False):
            with self.subTest(protected=protected):
                objects, candidates, first, native = self.fixture()
                if protected:
                    first['categories']['logo'] = ['G006']
                    candidates.append(dict(candidates[-1], id='G006', member_ids=['wide']))
                else:
                    native.update(bbox=[10, 0, 210, 50], area=10000)
                attach_native_contexts(candidates, [native])
                audit, _ = residual_surface_candidates(objects, candidates, first)
                self.assertFalse(any(c['native_type']=='NativeDecorationGroup' for c in audit))


if __name__ == '__main__':
    unittest.main()
