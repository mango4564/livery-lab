"""A vehicle's structural subgroup must not split its classified stripe."""
from copy import deepcopy
from pathlib import Path
import tempfile
import tkinter as tk
from unittest.mock import patch

from core.source_regions import normalize_source_regions, object_regions, split_by_region
from review_gallery import ReviewGallery
from reviewed_exports import LABELS, export_reviewed
from test_reviewed_exports import fixture
from tests.tk_lifecycle import TkTestCase


def regions():
    return [
        {'id': 'R1', 'name': '左视图车辆区', 'source_view': '左视图', 'anchor_ids': ['G1'],
         'bbox': [-5, -5, 55, 15], 'selected_ids': ['0', '1']},
        {'id': 'R2', 'name': '右视图车辆区', 'source_view': '右视图', 'anchor_ids': ['G2'],
         'bbox': [55, -5, 135, 15], 'selected_ids': ['3', '4', '6']},
        {'id': 'R3', 'name': '顶部彩条图案区', 'source_view': '独立图案', 'anchor_ids': ['G3'],
         'bbox': [95, -1, 130, 10], 'selected_ids': ['6']},
    ]


class NestedSourceRegionTests(TkTestCase):
    def test_only_redundant_subregion_is_removed_without_mutation(self):
        source = regions()
        original = deepcopy(source)
        self.assertEqual([r['id'] for r in normalize_source_regions(source)], ['R1', 'R2'])
        self.assertEqual(source, original)
        for mutation in ('different_ids', 'outside', 'vehicle_view', 'ambiguous_parent'):
            changed = deepcopy(source)
            if mutation == 'different_ids':
                changed[2]['selected_ids'] = ['external-copy']
            elif mutation == 'outside':
                changed[2]['bbox'] = [80, -1, 140, 10]
            elif mutation == 'vehicle_view':
                changed[2]['source_view'] = '右视图'
            else:
                changed.append(dict(changed[1], id='R4'))
            with self.subTest(mutation=mutation):
                self.assertIn('R3', [r['id'] for r in normalize_source_regions(changed)])

    def test_old_record_gallery_and_export_keep_three_paths_per_side(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = fixture(root)
            result['objects'].append({'id': '6', 'd': 'M120 0H130V8H120Z',
                                      'bbox': [120, 0, 130, 8], 'fill': '#000000'})
            choices = {name: None for name in LABELS}
            choices['彩条'] = [str(i) for i in range(6)]
            result.update(component_categories=choices, source_regions=regions())
            original = deepcopy(result)
            # Reproduce the old 3/2/1 split: the last stripe has two owners.
            raw = object_regions(result['objects'], result['source_regions'])
            self.assertEqual([len(g) for g in split_by_region(choices['彩条'], raw)], [3, 2, 1])
            window = tk.Tk(); window.withdraw(); self.addCleanup(window.destroy)
            with patch.object(ReviewGallery, '_locator_worker', return_value=None):
                gallery = ReviewGallery(window, result, root/'out')
            gallery.withdraw(); self.addCleanup(gallery.destroy)
            gallery._switch_category('彩条')
            current = [c for c in gallery.filtered if c['id'].startswith('current-')]
            self.assertEqual([c['selected_ids'] for c in current], [['0', '1', '2'], ['3', '4', '5']])
            for candidate in current:
                gallery.current_candidate = candidate
                gallery._confirm()
            for name in LABELS:
                if name != '彩条':
                    gallery._switch_category(name); gallery._absent()
            # Even direct export of the untouched old region list uses the fix.
            published = export_reviewed(result, gallery.choices, root/'out', patterns=gallery.patterns)
            entries = published['manifest']['categories']['彩条']['patterns']
            self.assertEqual([e['source_region'] for e in entries], ['R1', 'R2'])
            self.assertEqual([len(e['selected_ids']) for e in entries], [3, 3])
            self.assertEqual(len(published['manifest']['categories']['彩条']['files']), 4)
            self.assertEqual(result, original)
