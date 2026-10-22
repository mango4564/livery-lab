"""Native wordmark proposals and consistent logo-free text review/export."""
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from tests.tk_lifecycle import TkTestCase
import xml.etree.ElementTree as ET

from components import build_candidates, resolve_categories
from core.paint import normalize_categories
from review_gallery import ReviewGallery
from reviewed_exports import export_reviewed
from test_components import decision
from test_reviewed_exports import fixture


class TextLogoSeparationTests(TkTestCase):
    def test_native_text_children_and_side_graphic_are_separate_candidates(self):
        boxes = [[0, 0, 10, 10], [1, 1, 3, 3], [14, 5, 20, 9],
                 [22, 5, 28, 9], [14, 0, 20, 2], [22, 0, 28, 2]]
        objects = [{'id': str(i), 'source_offset': offset, 'bbox': box, 'fill': 'none',
                    'd': 'M0 0H1V1Z'} for i, (offset, box) in enumerate(zip([20,30,50,60,75,85], boxes))]
        containers = [
            {'class_name':'AGroup', 'source_offset':0, 'end_offset':100},
            {'class_name':'AGroup', 'source_offset':10, 'end_offset':40},
            {'class_name':'AText', 'source_offset':40, 'end_offset':65},
            {'class_name':'AText', 'source_offset':65, 'end_offset':90}]
        candidates, coverage = build_candidates(objects, containers)
        text = next(c for c in candidates if c['native_type']=='WordmarkText')
        logo = next(c for c in candidates if c['native_type']=='WordmarkLogo')
        parent = next(c for c in candidates if len(c['member_ids'])==6)
        self.assertEqual(text['member_ids'], ['2', '3', '4', '5'])
        self.assertEqual(logo['member_ids'], ['0', '1'])
        self.assertEqual(coverage['provided_wordmark_candidates'], 2)
        resolved = resolve_categories(objects, candidates, decision(文字=[parent['id']], logo=[logo['id']]))
        self.assertEqual(resolved['文字'], text['member_ids'])
        self.assertEqual(resolved['logo'], logo['member_ids'])
        # A plain company-name group must not invent a graphical logo.
        plain, _ = build_candidates(objects[2:], containers[2:])
        self.assertFalse(any(c['native_type']=='WordmarkLogo' for c in plain))

    def test_explicit_logo_is_removed_from_text_but_other_conflicts_still_fail(self):
        objects = [{'id': str(i)} for i in range(3)]
        result = normalize_categories(objects, {'车身':['0','1','2'], '文字':['0','1'], 'logo':['0']})
        self.assertEqual(result['文字'], ['1'])
        self.assertEqual(result['车身'], ['2'])
        with self.assertRaises(ValueError):
            normalize_categories(objects, {'文字':['0','1'], '彩条':['1']})

    def test_raw_parent_preview_manual_editor_and_export_use_identical_text_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            result = fixture(path)
            result['component_categories'].update(文字=['2','3'], logo=['3'])
            root = tk.Tk()
            root.withdraw()
            try:
                gallery = ReviewGallery(root, result, path/'out')
                gallery.withdraw()
                try:
                    gallery._switch_category('文字')
                    self.assertEqual(gallery._effective_ids(gallery.current_candidate), ['2'])
                    self.assertIn('已扣除logo 1 条', gallery.tree.item('0', 'text'))
                    gallery._confirm()
                    gallery.pattern_list.selection_set(0)
                    gallery._preview_pattern()
                    self.assertEqual(gallery._effective_ids(gallery.current_candidate), ['2'])
                    gallery._switch_category('logo')
                    gallery._absent()
                    gallery._switch_category('文字')
                    self.assertEqual(gallery._effective_ids(gallery.current_candidate), ['2','3'])
                    gallery._switch_category('logo')
                    gallery.tree.selection_set('0')
                    gallery._preview()
                    for category in gallery.category_buttons:
                        if category not in ('文字','logo'):
                            gallery._switch_category(category)
                            gallery._absent()
                    published = export_reviewed(result, gallery.choices, path/'out', patterns=gallery.patterns)
                    for category, expected in [('文字',['2']), ('logo',['3'])]:
                        record = published['manifest']['categories'][category]
                        self.assertEqual(record['selected_ids'], expected)
                        tree = ET.parse(Path(published['output_dir'])/record['files'][0])
                        self.assertEqual([p.get('id') for p in tree.iter() if p.tag.endswith('}path')], expected)
                finally:
                    gallery.destroy()
            finally:
                root.destroy()
