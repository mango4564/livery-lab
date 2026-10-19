"""Real Tk selection gestures and reviewed exports, with no AI/network calls."""
import json
from pathlib import Path
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest

from review_gallery import ObjectSelectionEditor, ReviewGallery
from reviewed_exports import export_reviewed
from test_reviewed_exports import fixture


class ManualSelectionTests(unittest.TestCase):
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
        box = e.boxes['5']
        center = ((box[0]+box[2])/2, (box[1]+box[3])/2)
        self.gesture(center, center)
        self.assertEqual(e.selected, {'5'})
        e.tree.selection_set('0')
        e._change(set(e.tree.selection()))
        self.assertEqual(e.selected, {'0', '5'})

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
