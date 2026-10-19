"""Offline boundary tests for semantic group classification and source mapping."""
import unittest
from components import build_candidates, resolve_categories, CATEGORIES
from vision import _validate_components, VisionError


def decision(**overrides):
    categories = {name: [] for name in CATEGORIES}
    categories.update(overrides)
    return {'categories': categories, 'reason': 'test', 'confidence': .9}


class ComponentsTests(unittest.TestCase):
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
            resolve_categories([{'id': '1'}, {'id': '2'}], c, decision(logo=['a'], 文字=['b']))


if __name__ == '__main__':
    unittest.main()
