"""Replay selecting a vehicle parent containing already classified paint."""
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from tests.tk_lifecycle import TkTestCase
import xml.etree.ElementTree as ET

from review_gallery import ReviewGallery
from reviewed_exports import export_reviewed
from test_reviewed_exports import fixture


class ReviewBodySeparationTests(TkTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.result = fixture(Path(self.temp.name))
        self.result['component_categories']['车漆'] = ['5']
        self.result['component_candidates'] = [{
            'id': 'G002', 'label': 'G002 · AGroup', 'selected_ids': ['0', '1', '5'],
            'category_hints': ['车身'], 'recommended_categories': ['车身']}]
        self.gallery = ReviewGallery(self.root, self.result, Path(self.temp.name)/'out')
        self.gallery.withdraw()
        self.addCleanup(self.gallery.destroy)
        # The parent now shares a single default preview with the current body.
        self.assertEqual(len(self.gallery.filtered), 1)
        self.select_parent()

    def select_parent(self):
        g = self.gallery
        g.show_all.set(True)
        g._switch_category('车身')
        index = next(i for i, c in enumerate(g.filtered) if c['id'] == 'G002')
        g.tree.selection_set(str(index))
        g._preview()
        # Following category switches should use their normal candidates.
        g.show_all.set(False)

    def visible_ids(self):
        svg = self.gallery._svg(self.gallery.current_candidate)
        return [n.get('id') for n in ET.fromstring(svg).iter()
                if n.tag.endswith('}path')]

    def test_parent_preview_and_added_pattern_match_final_body_export(self):
        g = self.gallery
        self.assertEqual(g.current_candidate['id'], 'G002')
        self.assertEqual(self.visible_ids(), ['0'])
        self.assertIn('已扣除车漆等 2 条', g.tree.item('1', 'text'))
        g._confirm()
        g.pattern_list.selection_set(0)
        g._preview_pattern()
        self.assertEqual(self.visible_ids(), ['0'])
        for category in g.category_buttons:
            if category != '车身':
                g._switch_category(category)
                g._confirm()
        exported = export_reviewed(self.result, g.choices, Path(self.temp.name)/'out', patterns=g.patterns)
        record = exported['manifest']['categories']['车身']
        self.assertEqual(record['selected_ids'], ['0'])
        self.assertEqual(record['patterns'][0]['selected_ids'], ['0'])
        root = ET.parse(Path(exported['output_dir'])/record['files'][0])
        self.assertEqual([n.get('id') for n in root.iter() if n.tag.endswith('}path')], ['0'])
        # Raw source ownership remains available for later corrections.
        self.assertEqual(self.result['component_candidates'][0]['selected_ids'], ['0', '1', '5'])

    def test_user_paint_changes_override_ai_and_invalidate_body_thumbnail(self):
        g = self.gallery
        self.assertIn(('0',), g.thumbnail_cache)
        g._switch_category('车漆')
        g._absent()
        g._switch_category('车身')
        self.select_parent()
        self.assertEqual(self.visible_ids(), ['0', '5'])
        self.assertIn(('0', '5'), g.thumbnail_cache)
        g._switch_category('车漆')
        g._confirm()
        g._switch_category('车身')
        self.select_parent()
        self.assertEqual(self.visible_ids(), ['0'])
        g._edit_objects()
        from review_gallery import ObjectSelectionEditor
        editor = next(w for w in g.winfo_children() if isinstance(w, ObjectSelectionEditor))
        self.assertEqual(editor.selected, {'0'})
        editor.destroy()


if __name__ == '__main__':
    unittest.main()
