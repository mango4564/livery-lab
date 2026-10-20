"""Offline review filtering: remove duplicate memberships, retain all regions."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from core.source_regions import object_regions, region_for_ids
from components import CATEGORIES, review_candidates
from review_gallery import ReviewGallery, classified_patterns


def gallery_state(objects, candidates, regions=(), categories=None):
    assignments = object_regions(objects, regions)
    g = SimpleNamespace(
        category='车身', candidates=candidates, by_id={o['id']: o for o in objects},
        result={'component_categories': categories or {}}, choices={},
        show_all=Mock(), heading=Mock(), _render_page=Mock(), _refresh_status=Mock())
    g.show_all.get.return_value = False
    g._region = lambda ids: region_for_ids(ids, assignments, regions)
    g._effective_ids = lambda c: ReviewGallery._effective_ids(g, c)
    return g


class ReviewCandidateDedupTests(unittest.TestCase):
    def test_multiple_marks_in_one_region_keep_separate_complete_candidates(self):
        objects = [{'id': str(i)} for i in range(6)]
        for category in ('logo', '门徽'):
            candidates = [dict(id='G'+str(i), selected_ids=ids,
                               category_hints=[category], recommended_categories=[category])
                          for i, ids in enumerate((['0', '1'], ['2', '3'], ['4', '5'], ['0']))]
            before = deepcopy(candidates)
            groups = classified_patterns(category, [str(i) for i in range(6)], candidates)
            self.assertEqual(groups, [['0', '1'], ['2', '3'], ['4', '5']])
            currents = [dict(id=f'current-{i}', selected_ids=ids, category_hints=[category])
                        for i, ids in enumerate(groups)]
            g = gallery_state(objects, currents+candidates)
            ReviewGallery._switch_category(g, category)
            self.assertEqual([c['selected_ids'] for c in g.filtered], groups)
            self.assertEqual(candidates, before)
        self.assertEqual(classified_patterns('logo', ['0', '1', '2'], candidates),
                         [['0', '1', '2']])  # No explicit logo evidence: legacy fallback.

    def test_geometry_proposals_do_not_become_graphic_classifications(self):
        candidates = [dict(id='G'+str(i), native_type=kind, member_ids=[str(i)],
                           bbox=[0, 0, 100, 10], area=1000, aspect=10)
                      for i, kind in enumerate(('AGroup', 'PaintSurface', 'PaintRegion',
                                                'EmblemLogo', 'WordmarkLogo', 'SpatialPaths'))]
        decision = {'categories': {name: [] for name in CATEGORIES}}
        records = review_candidates(candidates, decision)
        self.assertTrue(all(not c['category_hints'] for c in records))
        self.assertEqual(len(records), len(candidates))  # Still available manually.
        for name in ('彩条', 'logo', '车漆'):
            decision['categories'][name] = ['G0']
            record = review_candidates(candidates, decision)[0]
            self.assertIn(name, record['recommended_categories'])
            self.assertIn(name, record['category_hints'])
            decision['categories'][name] = []

    def test_old_geometry_hints_are_hidden_and_manual_selection_is_preserved(self):
        for name in ('彩条', 'logo', '车漆'):
            with self.subTest(category=name):
                objects = [{'id': str(i)} for i in range(5)]
                candidates = [dict(id='old-'+str(i), selected_ids=[str(i)], category_hints=[name])
                              for i in range(4)]
                candidates.append(dict(id='manual-'+name, selected_ids=['4'], category_hints=[name]))
                g = gallery_state(objects, candidates)
                ReviewGallery._switch_category(g, name)
                self.assertEqual([c['id'] for c in g.filtered], ['manual-'+name])
                g.show_all.get.return_value = True
                ReviewGallery._switch_category(g, name)
                self.assertEqual(len(g.filtered), 5)

    def test_paint_fragments_are_covered_by_complete_region_patterns(self):
        objects = [dict(id=str(i), bbox=[i//2*30, 0, i//2*30+20, 10]) for i in range(4)]
        regions = [dict(id='R'+str(i), bbox=[i*30, 0, i*30+20, 10],
                        selected_ids=[str(2*i), str(2*i+1)]) for i in range(2)]
        candidates = [dict(id='current-车漆-region-'+str(i), selected_ids=r['selected_ids'],
                           category_hints=['车漆']) for i, r in enumerate(regions)]
        candidates.extend(dict(id='surface-'+o['id'], selected_ids=[o['id']],
                               category_hints=['车漆'], recommended_categories=['车漆']) for o in objects)
        before = deepcopy(candidates)
        g = gallery_state(objects, candidates, regions)
        ReviewGallery._switch_category(g, '车漆')
        self.assertEqual(len(g.filtered), 2)
        self.assertEqual({i for c in g.filtered for i in c['selected_ids']}, {'0', '1', '2', '3'})
        g.show_all.get.return_value = True
        ReviewGallery._switch_category(g, '车漆')
        self.assertEqual(len(g.filtered), 6)
        self.assertEqual(candidates, before)

    def variants(self, category='车身'):
        objects, candidates, regions = [], [], []
        for index, direction in enumerate(('右视图', '左视图', '前视图', '后视图') * 2):
            oid = str(index)
            bounds = [index * 30, 0, index * 30 + 20, 10]
            objects.append({'id': oid, 'bbox': bounds})
            regions.append(dict(id='R'+oid, name=direction+oid, source_view=direction,
                                bbox=bounds, selected_ids=[oid]))
            candidates.append(dict(id='G'+oid, selected_ids=[oid], category_hints=[category],
                                   recommended_categories=[category]))
        return objects, candidates, regions

    def test_complete_emblem_hides_partial_arc_but_preserves_manual_and_raw_access(self):
        objects = [{'id': str(i)} for i in range(5)]
        candidates = [dict(id='current-门徽', selected_ids=['0', '1', '2', '3', '4'],
                           category_hints=['门徽']),
                      dict(id='arc', selected_ids=['0', '1'], category_hints=['门徽']),
                      dict(id='manual-arc', selected_ids=['0'], category_hints=['门徽'])]
        g = gallery_state(objects, candidates)
        ReviewGallery._switch_category(g, '门徽')
        self.assertEqual([c['id'] for c in g.filtered], ['current-门徽', 'manual-arc'])
        g.show_all.get.return_value = True
        ReviewGallery._switch_category(g)
        self.assertEqual(len(g.filtered), 3)

    def test_eight_regions_remain_visible_while_duplicate_groups_are_removed(self):
        objects, candidates, regions = self.variants()
        candidates.extend(dict(c, id='native-'+c['id']) for c in list(candidates))
        before = deepcopy(candidates)
        g = gallery_state(objects, candidates, regions)
        ReviewGallery._switch_category(g)
        self.assertEqual([c['id'] for c in g.filtered], ['G'+str(i) for i in range(8)])
        self.assertEqual(len(g.filtered), 8)
        self.assertEqual(candidates, before)
        g.show_all.get.return_value = True
        ReviewGallery._switch_category(g)
        self.assertEqual(len(g.filtered), 8)

    def test_other_categories_keep_distinct_graphics_in_same_direction(self):
        for category in ('彩条', '文字', 'logo', '门徽', '车漆'):
            with self.subTest(category=category):
                g = gallery_state(*self.variants(category))
                ReviewGallery._switch_category(g, category)
                self.assertEqual(len(g.filtered), 8)

    def test_unknown_regions_and_saved_patterns_are_not_folded(self):
        objects, candidates, regions = self.variants()
        g = gallery_state(objects, candidates)
        ReviewGallery._switch_category(g)
        self.assertEqual(len(g.filtered), 8)
        for region in regions:
            region['source_view'] = '待确认视图'
        g = gallery_state(objects, candidates, regions)
        ReviewGallery._switch_category(g)
        self.assertEqual(len(g.filtered), 8)
        objects, candidates, regions = self.variants()
        candidates[4]['id'] = 'saved-车身-1'
        g = gallery_state(objects, candidates, regions)
        ReviewGallery._switch_category(g)
        self.assertEqual(len(g.filtered), 8)

    def test_effective_dedup_tracks_paint_reclassification_and_raw_access(self):
        objects = [{'id': str(i)} for i in range(3)]
        candidates = [dict(id='current', selected_ids=['0'], category_hints=['车身']),
                      dict(id='parent', selected_ids=['0', '1'], category_hints=['车身'])]
        g = gallery_state(objects, candidates, categories={'车漆': ['1']})
        ReviewGallery._switch_category(g)
        self.assertEqual(len(g.filtered), 1)
        g.choices['车漆'] = None
        ReviewGallery._switch_category(g)
        self.assertEqual(len(g.filtered), 2)
        g.choices['车漆'] = ['1']
        g.show_all.get.return_value = True
        ReviewGallery._switch_category(g)
        self.assertEqual(len(g.filtered), 2)


if __name__ == '__main__':
    unittest.main()
