"""Offline boundary tests for semantic group classification and source mapping."""
import unittest
from components import build_candidates, resolve_categories, CATEGORIES
from vision import _validate_components, VisionError


def decision(**overrides):
    categories = {name: [] for name in CATEGORIES}
    categories.update(overrides)
    return {'categories': categories, 'reason': 'test', 'confidence': .9}


class ComponentsTests(unittest.TestCase):
    def test_ungrouped_curves_cover_both_sides_without_reference(self):
        objects = []
        for side, y in enumerate((0, 100)):
            for i in range(3):
                objects.append({'id': f'{side}-{i}', 'source_offset': len(objects)*10,
                                'bbox': [i*10, y, 100, y+20+i], 'fill': '#999999',
                                'd': 'M0 0 C10 2 20 3 30 0 Z'})
        candidates, coverage = build_candidates(objects, [])
        bundles = [set(c['member_ids']) for c in candidates if c['native_type']=='SpatialPaths']
        self.assertIn({'0-0', '0-1', '0-2'}, bundles)
        self.assertIn({'1-0', '1-1', '1-2'}, bundles)
        self.assertFalse(coverage['reference_library_used'])
        picked = next(c for c in candidates if set(c['member_ids'])=={'0-0','0-1','0-2'})
        result = resolve_categories(objects, candidates, decision(彩条=[picked['id']]))
        self.assertEqual(set(result['彩条']), {'0-0','0-1','0-2'})

    def test_single_paths_allow_multicolour_selection_and_respect_limit(self):
        objects = [{'id': str(i), 'source_offset': i*10, 'bbox': [0, 0, 100, 10+i],
                    'fill': color, 'd': 'M0 0 C10 2 20 3 30 0 Z'}
                   for i, color in enumerate(('#ffffff', '#aabbcc', '#111111'))]
        # A distant extent supplies page scale without becoming a candidate.
        objects.append({'id':'extent', 'source_offset':100, 'bbox':[0,0,200,100], 'd':''})
        candidates, _ = build_candidates(objects, [], limit=3)
        self.assertEqual(len(candidates), 3)
        result = resolve_categories(objects, candidates, decision(彩条=[c['id'] for c in candidates]))
        self.assertEqual(set(result['彩条']), {'0','1','2'})

    def test_unknown_group_or_missing_category_rejected(self):
        with self.assertRaises(VisionError):
            _validate_components(decision(logo=['invented']), {'G1'})
        invalid = decision()
        del invalid['categories']['门徽']
        with self.assertRaises(VisionError):
            _validate_components(invalid, set())

    def test_same_group_cannot_have_two_labels(self):
        with self.assertRaises(VisionError):
            _validate_components(decision(logo=['G1'], 文字=['G1']), {'G1'})

    def test_whole_compound_is_a_candidate_at_its_own_offset(self):
        obj = {'id': 'p', 'source_offset': 20, 'bbox': [0, 0, 20, 10],
               'd': 'M0 0 L20 0 Z M2 2 L3 3 Z M5 5 L6 6 Z'}
        groups = [{'class_name': 'AComposite', 'source_offset': 20, 'end_offset': 100}]
        candidates, _ = build_candidates([obj], groups)
        self.assertEqual(candidates[0]['member_ids'], ['p'])

    def test_arc_child_removes_technical_text_and_body_overlap(self):
        objects = [{'id': str(i)} for i in range(4)]
        candidates = [
            {'id': 'body', 'native_type': 'AGroup', 'member_ids': ['0', '1', '2', '3']},
            {'id': 'door', 'native_type': 'AGroup', 'member_ids': ['1', '2']},
            {'id': 'arc', 'native_type': 'AArcText', 'member_ids': ['1']}]
        result = resolve_categories(objects, candidates, decision(车身=['body'], 门徽=['door']))
        self.assertEqual(result['门徽'], ['1'])
        self.assertEqual(result['车身'], ['0', '2', '3'])

    def test_overlapping_nonbody_groups_rejected(self):
        c = [{'id': 'a', 'native_type': 'AGroup', 'member_ids': ['1']},
             {'id': 'b', 'native_type': 'AGroup', 'member_ids': ['1', '2']}]
        with self.assertRaises(ValueError):
            resolve_categories([{'id': '1'}, {'id': '2'}], c, decision(logo=['a'], 彩条=['b']))


if __name__ == '__main__':
    unittest.main()
