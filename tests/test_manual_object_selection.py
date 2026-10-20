"""Real Tk selection gestures and reviewed exports, with no AI/network calls."""
import json
from pathlib import Path
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest
from tests.tk_lifecycle import TkTestCase

from review_gallery import ObjectSelectionEditor, ReviewGallery
from reviewed_exports import LABELS, export_reviewed
from test_reviewed_exports import fixture


class ManualSelectionTests(TkTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.root.geometry('200x100')
        self.addCleanup(self.root.destroy)
        self.result = fixture(Path(self.temp.name))
        self.accepted = []
        self.editor = ObjectSelectionEditor(self.root, self.result['objects'], [], 'logo', self.accepted.append)
        self.root.update()

    def gesture(self, start, end):
        self.editor._press(SimpleNamespace(x=start[0], y=start[1]))
        self.editor._motion(SimpleNamespace(x=end[0], y=end[1]))
        self.editor._release(SimpleNamespace(x=end[0], y=end[1]))

    def test_point_and_box_after_zoom_and_pan_then_undo(self):
        e = self.editor
        e.mode.set('add')
        e.viewer.set_scale(e.viewer.scale * 1.7)
        e.viewer.offset = (e.viewer.offset[0] + 43, e.viewer.offset[1] - 17)
        e.viewer.draw()
        box = e.boxes['5']
        center = ((box[0]+box[2])/2, (box[1]+box[3])/2)
        self.gesture(center, center)
        self.assertEqual(e.selected, {'5'})
        # A reversed drag must still select all fully enclosed source objects.
        a, b = e.boxes['2'], e.boxes['3']
        self.gesture((b[2]+1, b[3]+1), (a[0]-1, a[1]-1))
        self.assertEqual(e.selected, {'2', '3', '5'})
        e.mode.set('remove')
        self.gesture(center, center)
        self.assertEqual(e.selected, {'2', '3'})
        e._undo()
        self.assertEqual(e.selected, {'2', '3', '5'})
        e._apply()
        self.assertEqual(self.accepted, [['2', '3', '5']])

    def test_cancel_and_empty_selection_never_confirm(self):
        self.editor._apply()
        self.assertEqual(self.accepted, [])
        self.editor._change({'5'})
        self.editor.destroy()
        self.assertEqual(self.accepted, [])

    def test_overlapping_body_and_detail_remain_selectable(self):
        e = self.editor
        e.destroy()
        objects = [dict(self.result['objects'][0], bbox=[0, 0, 120, 8]), self.result['objects'][5]]
        e = self.editor = ObjectSelectionEditor(self.root, objects, [], 'logo', self.accepted.append)
        self.root.update()
        e.mode.set('add')
        box = e.boxes['5']
        center = ((box[0]+box[2])/2, (box[1]+box[3])/2)
        self.gesture(center, center)
        self.assertEqual(e.selected, {'5'})
        e.tree.selection_set('0')
        e._change(set(e.tree.selection()))
        self.assertEqual(e.selected, {'0', '5'})

    def test_default_pan_moves_view_without_changing_selection(self):
        e = self.editor
        e._change({'5'})
        self.assertEqual(e.mode.get(), 'pan')
        offset = e.viewer.offset
        box = e.boxes['5']
        center = ((box[0]+box[2])/2, (box[1]+box[3])/2)
        self.gesture(center, (center[0]+20, center[1]+10))
        self.assertEqual(e.selected, {'5'})
        self.assertEqual(e.viewer.offset, (offset[0]+20, offset[1]+10))
        self.assertEqual(len(e.history), 1)

    def test_cancel_selected_objects_by_click_and_box_then_undo(self):
        e = self.editor
        e._change({'1', '5'})
        e.mode.set('remove')
        box = e.boxes['5']
        center = ((box[0]+box[2])/2, (box[1]+box[3])/2)
        self.gesture(center, center)
        self.assertEqual(e.selected, {'1'})
        # Clicking again must not select the cancelled object.
        self.gesture(center, center)
        self.assertEqual(e.selected, {'1'})
        box = e.boxes['1']
        self.gesture((box[0]-1, box[1]-1), (box[2]+1, box[3]+1))
        self.assertEqual(e.selected, set())
        self.assertEqual(str(e.apply_button['state']), 'disabled')
        e._undo()
        self.assertEqual(e.selected, {'1'})
        e._undo()
        self.assertEqual(e.selected, {'1', '5'})

    def test_cancel_overlapping_selected_body_ignores_unselected_detail(self):
        self.editor.destroy()
        objects = [dict(self.result['objects'][0], bbox=[0, 0, 120, 8]), self.result['objects'][5]]
        e = self.editor = ObjectSelectionEditor(self.root, objects, ['0'], 'logo', self.accepted.append)
        self.root.update()
        e.mode.set('remove')
        box = e.boxes['5']
        center = ((box[0]+box[2])/2, (box[1]+box[3])/2)
        self.gesture(center, center)
        self.assertEqual(e.selected, set())
        e._undo()
        self.assertEqual(e.selected, {'0'})

    def test_start_over_clears_selection_and_allows_new_box_selection(self):
        e = self.editor
        e._change({'5'})
        e.tree.selection_set('5')
        e._start_over()
        self.assertEqual(e.selected, set())
        self.assertEqual(e.tree.selection(), ())
        self.assertEqual(e.mode.get(), 'add')
        e._undo()
        self.assertEqual(e.selected, {'5'})
        e._start_over()
        box = e.boxes['1']
        self.gesture((box[0]-1, box[1]-1), (box[2]+1, box[3]+1))
        self.assertEqual(e.selected, {'1'})
        # Redrawing must remove obsolete selection boxes without accumulating overlays.
        for _ in range(3):
            e.viewer.draw()
        self.assertEqual(len(e.viewer.canvas.find_withtag('selection')), 1)

    def test_new_pattern_button_starts_empty_and_preserves_existing_patterns(self):
        self.editor.destroy()
        g = ReviewGallery(self.root, self.result, Path(self.temp.name)/'out')
        self.addCleanup(g.destroy)
        g._switch_category('logo')
        g._accept_manual('logo', ['5'])
        original = list(g.choices['logo'])
        self.assertTrue(g.current_candidate['selected_ids'])
        g.new_pattern_button.invoke()
        dialog = next(w for w in g.winfo_children() if isinstance(w, ObjectSelectionEditor))
        self.assertEqual(dialog.selected, set())
        self.assertEqual(dialog.mode.get(), 'pan')
        self.assertEqual(set(dialog.by_id), set(g.by_id))
        dialog.destroy()
        self.assertEqual(g.choices['logo'], original)
        g.new_pattern_button.invoke()
        dialog = next(w for w in g.winfo_children() if isinstance(w, ObjectSelectionEditor))
        dialog._change({'1'})
        dialog.category.set('logo')
        dialog._apply()
        self.assertEqual(set(g.choices['logo']), {'1', '5'})
        self.assertEqual(len(g.patterns['logo']), 2)

    def test_start_from_scratch_with_no_candidates(self):
        self.editor.destroy()
        result = dict(self.result, component_categories={}, component_candidates=[], views=[])
        g = ReviewGallery(self.root, result, Path(self.temp.name)/'out')
        self.addCleanup(g.destroy)
        self.assertIsNone(g.current_candidate)
        g.new_pattern_button.invoke()
        dialog = next(w for w in g.winfo_children() if isinstance(w, ObjectSelectionEditor))
        self.assertEqual(dialog.selected, set())
        dialog._change({'5'})
        dialog.category.set('车身')
        dialog._apply()
        self.assertEqual(g.choices[g.category], ['5'])

    def test_free_selection_requires_category_and_accepts_each_pattern_type(self):
        self.editor.destroy()
        g = ReviewGallery(self.root, self.result, Path(self.temp.name)/'out')
        self.addCleanup(g.destroy)
        for category, label in LABELS.items():
            with self.subTest(category=category):
                g._switch_category('车身')
                before = dict(g.choices)
                g.new_pattern_button.invoke()
                dialog = next(w for w in g.winfo_children() if isinstance(w, ObjectSelectionEditor))
                self.assertEqual(dialog.category.get(), '')
                self.assertEqual(set(dialog.category_combo['values']), set(LABELS.values()))
                dialog._change({'5'})
                self.assertEqual(str(dialog.apply_button['state']), 'disabled')
                dialog._apply()
                self.assertEqual(g.choices, before)
                self.assertTrue(dialog.winfo_exists())
                dialog.category.set(label)
                self.assertEqual(dialog.selected, {'5'})
                self.assertEqual(str(dialog.apply_button['state']), 'normal')
                self.assertIn(label, dialog.apply_button['text'])
                dialog.apply_button.invoke()
                self.assertEqual(g.category, category)
                self.assertEqual(g.choices[category], ['5'])
                self.assertEqual(g.patterns[category][0]['selected_ids'], ['5'])
                for other, ids in before.items():
                    if other != category:
                        self.assertEqual(g.choices[other], ids)

    def test_category_change_is_only_applied_on_confirmation(self):
        self.editor.destroy()
        g = ReviewGallery(self.root, self.result, Path(self.temp.name)/'out')
        self.addCleanup(g.destroy)
        g._switch_category('车身')
        before = dict(g.choices)
        g._edit_objects()
        dialog = next(w for w in g.winfo_children() if isinstance(w, ObjectSelectionEditor))
        self.assertEqual(dialog.category.get(), '车身')
        dialog.category.set('徽章')
        dialog._change({'5'})
        dialog.destroy()
        self.assertEqual(g.choices, before)
        self.assertEqual(g.category, '车身')

    def test_manual_choice_export_and_restore(self):
        self.editor.destroy()
        output = Path(self.temp.name)/'out'
        g = ReviewGallery(self.root, self.result, output)
        self.addCleanup(g.destroy)
        original = dict(g.choices)
        g._switch_category('logo')
        g._edit_objects()
        dialog = next(w for w in g.winfo_children() if isinstance(w, ObjectSelectionEditor))
        dialog._change({'5'}, replace=True)
        dialog.destroy()
        self.assertEqual(g.choices, original)
        g._accept_manual('logo', ['5'])
        self.assertEqual(g.choices['logo'], ['5'])
        self.assertEqual(g.current_candidate['selected_ids'], ['5'])
        for category in g.category_buttons:
            if category != 'logo':
                g._switch_category(category)
                g._absent()
        published = export_reviewed(self.result, g.choices, output)
        record = json.loads((Path(published['output_dir'])/'复核记录.json').read_text(encoding='utf8'))
        self.assertEqual(record['categories']['logo']['selected_ids'], ['5'])
        restored = ReviewGallery(self.root, self.result, output)
        self.addCleanup(restored.destroy)
        self.assertEqual(restored.choices['logo'], ['5'])


if __name__ == '__main__':
    unittest.main()
