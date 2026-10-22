"""Offline AI-result adoption, whole-plan confirmation and real export checks."""
import json
from pathlib import Path
import tempfile
import time
import tkinter as tk
from unittest.mock import patch

from review_gallery import ReviewGallery
from reviewed_exports import export_reviewed
from test_reviewed_exports import fixture
from tests.tk_lifecycle import TkTestCase


class AutoSelectionTests(TkTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.result = fixture(Path(self.temp.name))
        self.result.update(method='vision_six_components',
                           component_candidates=[{'id': 'extra', 'label': '未选中的候选',
                               'selected_ids': ['5'], 'category_hints': ['logo']}])
        self.result['component_categories']['车漆'] = []
        self.output = Path(self.temp.name)/'out'

    def open_gallery(self):
        with patch('vision.recognize', side_effect=AssertionError('Unexpected AI request')):
            gallery = ReviewGallery(self.root, self.result, self.output)
        gallery.withdraw()
        self.addCleanup(gallery.destroy)
        self.root.update_idletasks()
        return gallery

    def finish_export(self, gallery):
        deadline = time.monotonic()+20
        while gallery.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.02)
        self.assertFalse(gallery.busy)

    def test_prefills_all_selected_ids_but_never_assumes_absence(self):
        self.result['component_categories']['彩条'] = ['1', '5']
        g = self.open_gallery()
        self.assertEqual(g.choices['彩条'], ['1', '5'])
        self.assertNotIn('车漆', g.choices)
        self.assertEqual(g.ai_empty, {'车漆'})
        self.assertEqual(len(g.auto_selected), 5)
        self.assertFalse(hasattr(g, 'tabs'))
        g._open_details('彩条')
        self.assertEqual(g.category, '彩条')
        self.assertEqual(g.tree.selection(), ('0',))
        g._remove_pattern()  # No selected list row: must leave the plan untouched.
        self.assertEqual(g.choices['彩条'], ['1', '5'])

    def test_cancel_empty_confirmation_does_not_change_selection_or_export(self):
        g = self.open_gallery()
        before = dict(g.choices)
        with patch('review_gallery.messagebox.askyesno', return_value=False) as ask:
            g._export()
        ask.assert_called_once()
        self.assertEqual(g.choices, before)
        self.assertFalse(self.output.exists())
        self.assertFalse(g.busy)

    def test_prefill_splits_regions_and_keeps_each_region_direction(self):
        self.result['component_categories']['彩条'] = ['1', '5']
        self.result['source_regions'] = [
            {'id': 'R1', 'name': '左侧', 'source_view': '左视图', 'anchor_ids': ['g1'],
             'selected_ids': ['1'], 'bbox': [20, 0, 30, 8]},
            {'id': 'R2', 'name': '右侧', 'source_view': '右视图', 'anchor_ids': ['g2'],
             'selected_ids': ['5'], 'bbox': [100, 0, 110, 8]}]
        g = self.open_gallery()
        entries = g.patterns['彩条']
        self.assertEqual([e['selected_ids'] for e in entries], [['1'], ['5']])
        self.assertEqual([e['source_view'] for e in entries], ['左视图', '右视图'])
        self.assertEqual([e['region_name'] for e in entries], ['左侧', '右侧'])

    def test_minimum_window_opens_directly_with_candidates_preview_and_export_visible(self):
        g = self.open_gallery()
        g.geometry('1100x720+0+0')
        g.deiconify()
        self.root.update()
        self.assertGreater(g.preview.canvas.winfo_height(), 80)
        self.assertTrue(g.export_button.winfo_ismapped())
        self.assertLess(g.export_button.winfo_rootx()+g.export_button.winfo_width(),
                        g.winfo_rootx()+g.winfo_width()+1)
        self.assertTrue(g.tree.winfo_ismapped())
        self.assertTrue(g.new_pattern_button.winfo_ismapped())

    def test_all_six_classes_export_without_individual_confirmation(self):
        self.result['component_categories']['车漆'] = ['5']
        g = self.open_gallery()
        with patch('review_gallery.messagebox.askyesno') as ask, \
                patch('review_gallery.messagebox.showinfo'), \
                patch('review_gallery.messagebox.showerror') as error:
            g._export()
            self.finish_export(g)
            ask.assert_not_called()
            error.assert_not_called()
        folder = self.output/Path(self.result['source']).stem
        self.assertTrue((folder/'整车底图'/'vehicle-base.svg').is_file())
        self.assertTrue((folder/'整车底图'/'vehicle-base.png').is_file())

    def test_manual_removal_does_not_silently_reinsert_ai_selection(self):
        g = self.open_gallery()
        g._open_details('彩条')
        g.pattern_list.selection_set(0)
        g._remove_pattern()
        self.assertIsNone(g.choices['彩条'])
        self.assertNotIn('彩条', g.auto_selected)
        g._switch_category('车身')
        g._switch_category('彩条')
        self.assertIsNone(g.choices['彩条'])

    def test_one_collective_confirmation_exports_real_selected_patterns(self):
        g = self.open_gallery()
        with patch('review_gallery.messagebox.askyesno', return_value=True) as ask, \
                patch('review_gallery.messagebox.showinfo'), \
                patch('review_gallery.messagebox.showerror') as error:
            g._export()
            self.finish_export(g)
            ask.assert_called_once()
            error.assert_not_called()
        manifest = json.loads((self.output/Path(self.result['source']).stem/'复核记录.json').read_text(encoding='utf8'))
        self.assertEqual(manifest['categories']['车漆']['status'], 'confirmed_absent')
        self.assertEqual(manifest['categories']['彩条']['selected_ids'], ['1'])
        self.assertEqual(len(manifest['categories']['彩条']['patterns']), 1)
        self.assertIsNone(g.choices['车漆'])
        self.assertFalse(g.auto_selected)

    def test_saved_human_choices_and_absence_override_ai(self):
        choices = dict(self.result['component_categories'], 彩条=['5'], logo=None, 车漆=None)
        export_reviewed(self.result, choices, self.output)
        g = self.open_gallery()
        self.assertEqual(g.choices['彩条'], ['5'])
        self.assertIsNone(g.choices['logo'])
        self.assertIsNone(g.choices['车漆'])
        self.assertEqual(g.auto_selected, set())
        with patch('review_gallery.messagebox.askyesno') as ask, \
                patch('review_gallery.messagebox.showinfo'), \
                patch('review_gallery.messagebox.showerror') as error:
            g._export()
            self.finish_export(g)
            ask.assert_not_called()
            error.assert_not_called()

    def test_manual_results_remain_unselected(self):
        self.result['method'] = 'manual_six_components'
        g = self.open_gallery()
        self.assertEqual(g.choices, {})
        self.assertFalse(g.ai_empty)

    def test_invalid_ai_ids_are_blocked_rather_than_confirmed_absent(self):
        self.result['component_categories']['logo'] = ['unknown']
        g = self.open_gallery()
        with patch('review_gallery.messagebox.showinfo') as info, \
                patch('review_gallery.messagebox.askyesno') as ask:
            g._export()
        info.assert_called_once()
        ask.assert_not_called()
        self.assertFalse(self.output.exists())

    def multi_gallery(self):
        for name in self.result['component_categories']:
            self.result['component_categories'][name] = []
        self.result['component_categories']['彩条'] = ['0', '1', '2', '3']
        directions = ['左视图', '右视图', '前视图', '后视图', '顶视图', '底视图']
        self.result['source_regions'] = [
            {'id': f'R{i+1}', 'name': direction, 'source_view': direction,
             'anchor_ids': [f'G{i}'], 'selected_ids': [str(i)], 'bbox': o['bbox']}
            for i, (direction, o) in enumerate(zip(directions, self.result['objects']))]
        self.result['component_candidates'] = [
            {'id': 'extra-4', 'label': '其他图案', 'selected_ids': ['4'], 'category_hints': ['彩条']}]
        g = self.open_gallery()
        g._open_details('彩条')
        g.deiconify()
        self.root.update()
        return g

    def test_four_ai_patterns_are_native_selected_without_selecting_unadded_candidate(self):
        g = self.multi_gallery()
        self.assertEqual(g.tree.selection(), ('0', '1', '2', '3'))
        self.assertEqual(g.current_candidate['selected_ids'], ['0', '1', '2', '3'])
        self.assertEqual(len(g.current_candidate['_batch_candidates']), 4)
        self.assertFalse(hasattr(g, 'confirm_button'))
        self.assertIn('4 个图案', g.best_label.cget('text'))
        self.assertFalse(g.tree.item('0', 'tags'))
        self.assertNotIn('✓ 已加入导出', g.tree.item('4', 'text'))
        self.assertEqual(len(g.patterns['彩条']), 4)
        self.assertEqual([e['source_view'] for e in g.patterns['彩条']],
                         ['左视图', '右视图', '前视图', '后视图'])

    def test_real_ctrl_left_click_automatically_adds_and_removes_pattern(self):
        g = self.multi_gallery()
        x, y, width, height = g.tree.bbox('4')
        g.tree.event_generate('<Control-Button-1>', x=x+10, y=y+height//2)
        self.root.update()
        self.assertEqual(len(g.tree.selection()), 5)
        self.assertEqual(len(g.patterns['彩条']), 5)  # No add/confirm action is needed.
        self.root.update()
        self.assertEqual(len(g.patterns['彩条']), 5)
        self.assertFalse(g.tree.item('4', 'tags'))
        g.tree.event_generate('<Control-Button-1>', x=x+10, y=y+height//2)
        self.root.update()
        self.assertEqual(len(g.tree.selection()), 4)
        self.assertEqual(len(g.patterns['彩条']), 4)

    def test_added_pattern_ctrl_left_click_and_noncontiguous_bulk_removal(self):
        g = self.multi_gallery()
        for index in (0, 2):
            x, y, width, height = g.pattern_list.bbox(index)
            g.pattern_list.event_generate('<Control-Button-1>', x=x+5, y=y+height//2)
            self.root.update()
        self.assertEqual(g.pattern_list.curselection(), (0, 2))
        self.assertEqual(g.current_candidate['selected_ids'], ['0', '2'])
        g._remove_pattern()
        self.root.update()
        self.assertEqual(g.choices['彩条'], ['1', '3'])
        self.assertEqual(g.tree.selection(), ('1', '3'))
        self.assertNotIn('✓ 已加入导出', g.tree.item('0', 'text'))

    def test_select_all_and_delete_all_keeps_original_ai_result_unchanged(self):
        g = self.multi_gallery()
        g._select_all_patterns()
        self.assertEqual(g.pattern_list.curselection(), (0, 1, 2, 3))
        g._remove_pattern()
        self.root.update()
        self.assertEqual(g.patterns['彩条'], [])
        self.assertIsNone(g.choices['彩条'])
        self.assertEqual(g.tree.selection(), ())
        self.assertEqual(self.result['component_categories']['彩条'], ['0', '1', '2', '3'])
        self.assertNotIn('彩条', g.auto_selected)
        self.assertIsNone(g.current_candidate)

    def test_paging_keeps_multiple_picks_and_batch_add_keeps_regions(self):
        self.result['component_candidates'] = []
        with patch.object(ReviewGallery, 'PAGE_SIZE', 2):
            g = self.multi_gallery()
            self.assertEqual(g.tree.selection(), ('0', '1'))
            g._change_page(1)
            self.assertEqual(g.tree.selection(), ('2', '3'))
            self.assertEqual(len(g.current_candidate['_batch_candidates']), 4)
            g._change_page(1)
            g._select_all_candidates()
            self.assertEqual(len(g.current_candidate['_batch_candidates']), 5)
            self.assertEqual(len(g.patterns['彩条']), 5)
            self.assertEqual(g.patterns['彩条'][-1]['source_view'], '顶视图')

    def test_busy_select_all_and_delete_do_not_change_patterns(self):
        g = self.multi_gallery()
        before = [dict(e) for e in g.patterns['彩条']]
        g._select_all_patterns()
        g.busy = True
        g._select_all_candidates()
        self.assertEqual(g._delete_patterns(), 'break')
        self.assertEqual(g.patterns['彩条'], before)
        g.busy = False

    def test_windows_click_shift_range_and_ctrl_toggle(self):
        g = self.multi_gallery()
        for row, sequence in [('1', '<Button-1>'), ('3', '<Shift-Button-1>'), ('2', '<Control-Button-1>')]:
            x, y, width, height = g.tree.bbox(row)
            g.tree.event_generate(sequence, x=x+10, y=y+height//2)
            self.root.update()
            if row == '1':
                self.assertEqual(g.tree.selection(), ('1',))
            elif row == '3':
                self.assertEqual(g.tree.selection(), ('1', '2', '3'))
            else:
                self.assertEqual(g.tree.selection(), ('1', '3'))
        self.assertEqual(set(g.choices['彩条']), {'1', '3'})
        self.assertEqual(g.tree.bind('<Shift-Button-3>'), '')
        self.assertEqual(g.pattern_list.bind('<Shift-Button-3>'), '')

    def test_windows_ctrl_a_and_delete_keys_remove_all_added_patterns(self):
        g = self.multi_gallery()
        g.pattern_list.focus_force()
        self.root.update()
        g.pattern_list.event_generate('<Control-KeyPress-a>')
        self.root.update()
        self.assertEqual(g.pattern_list.curselection(), (0, 1, 2, 3))
        g.pattern_list.event_generate('<KeyPress-Delete>')
        self.root.update()
        self.assertEqual(g.patterns['彩条'], [])
        self.assertIsNone(g.choices['彩条'])

    def test_ctrl_a_selects_all_candidates_including_other_pages(self):
        with patch.object(ReviewGallery, 'PAGE_SIZE', 2):
            g = self.multi_gallery()
            g.tree.selection_set('0')
            g._preview()
            g.tree.focus_force()
            self.root.update()
            g.tree.event_generate('<Control-KeyPress-a>')
            self.root.update()
            self.assertEqual(len(g.current_candidate['_batch_candidates']), 5)
            g._change_page(2)
            self.assertEqual(g.tree.selection(), ('4',))
            self.assertEqual(len(g.current_candidate['_batch_candidates']), 5)

    def test_export_reads_highlight_immediately_without_add_or_event_loop(self):
        self.result['component_candidates'].append({'id': 'alt-stripe', 'label': '另一套彩条',
            'selected_ids': ['5'], 'category_hints': ['彩条']})
        g = self.open_gallery()
        g._switch_category('彩条')
        g.tree.selection_set('1')  # The selection event has not been drained yet.
        with patch('review_gallery.messagebox.askyesno', return_value=True), \
                patch('review_gallery.messagebox.showinfo'), \
                patch('review_gallery.messagebox.showerror') as error:
            g._export()
            self.finish_export(g)
            error.assert_not_called()
        manifest = json.loads((self.output/Path(self.result['source']).stem/'复核记录.json').read_text(encoding='utf8'))
        self.assertEqual(manifest['categories']['彩条']['selected_ids'], ['5'])

    def test_pending_highlight_survives_switching_categories_without_add(self):
        g = self.multi_gallery()
        g.tree.selection_set('4')
        g._switch_category('车身')
        self.assertEqual(g.choices['彩条'], ['4'])
        g._switch_category('彩条')
        self.assertEqual(g.tree.selection(), ('4',))
        self.root.update()
        self.assertEqual(g.choices['彩条'], ['4'])

    def test_unclassified_manual_preview_does_not_implicitly_select_first_candidate(self):
        self.result['method'] = 'manual_six_components'
        g = self.open_gallery()
        g.deiconify()
        self.root.update()
        self.assertEqual(g.tree.selection(), ())
        self.assertEqual(g.choices, {})
        g.tree.selection_set('0')
        self.root.update()
        self.assertEqual(g.choices['车身'], ['0'])
        g.tree.selection_remove('0')
        self.root.update()
        self.assertIsNone(g.choices['车身'])
        self.assertEqual(g.patterns['车身'], [])

    def test_manual_region_patterns_are_all_highlighted_and_survive_paging(self):
        g = self.multi_gallery()
        g._accept_manual('彩条', ['4', '5'])
        self.assertEqual(set(g.choices['彩条']), set('012345'))
        self.assertEqual(len(g.patterns['彩条']), 6)
        self.assertEqual({i for c in g._candidate_picks.values() for i in c['selected_ids']}, set('012345'))
        g._change_page(1)
        self.root.update()
        self.assertEqual(set(g.choices['彩条']), set('012345'))
        self.assertTrue(g.tree.selection())

    def test_source_direction_edits_save_automatically_and_survive_new_selection(self):
        g = self.multi_gallery()
        g.pattern_list.selection_set(1)
        g._preview_pattern()
        g.source_view.set('后视图')
        g.view_combo.event_generate('<<ComboboxSelected>>')
        self.assertEqual(g.patterns['彩条'][1]['source_view'], '后视图')
        g.tree.selection_add('4')
        self.root.update()
        self.assertEqual(len(g.patterns['彩条']), 5)
        self.assertEqual(g.patterns['彩条'][1]['source_view'], '后视图')
