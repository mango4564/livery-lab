"""Operate the real Tk review page without network calls or modal dialogs."""
from pathlib import Path
import tempfile
import tkinter as tk
import time
import unittest
from unittest.mock import patch
import json

from review_gallery import ReviewGallery
from test_reviewed_exports import fixture


class ReviewGalleryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=tk.Tk();self.root.withdraw();self.addCleanup(self.root.destroy)
        self.result=fixture(Path(self.temp.name))
        self.result['component_candidates']=[{'id':'other-stripe','label':'另一套彩条',
            'selected_ids':['5'],'category_hints':['彩条'],'recommended_categories':[]}]
        self.gallery=ReviewGallery(self.root,self.result,Path(self.temp.name)/'out')
        self.gallery.withdraw();self.root.update_idletasks()

    def test_category_comparison_confirmation_and_absence(self):
        g=self.gallery
        g._switch_category('彩条')
        self.assertEqual(len(g.filtered),2)
        g.tree.selection_set('1');g._preview();g._confirm()
        self.assertEqual(g.choices['彩条'],['5'])
        g.tree.selection_set('0');g._preview()
        self.assertEqual(g.choices['彩条'],['5'])
        g._switch_category('logo');g._absent()
        self.assertIsNone(g.choices['logo'])
        g._switch_category('车身')
        self.assertEqual(g.choices['彩条'],['5'])
        self.assertEqual(len(g.photos),1)

    def test_show_all_exposes_unclassified_groups_without_selecting_them(self):
        g=self.gallery
        g.show_all.set(True);g._switch_category('门徽')
        self.assertEqual(len(g.filtered),6)
        self.assertEqual(g.choices,{})

    def test_page_actions_export_the_confirmed_alternative_only(self):
        g=self.gallery
        for category in g.category_buttons:
            g._switch_category(category)
            if category in ('logo', '车漆'):
                g._absent()
            else:
                if category=='彩条':g.tree.selection_set('1');g._preview()
                g._confirm()
        with patch('review_gallery.messagebox.showinfo'), patch('review_gallery.messagebox.showerror') as error:
            g._export()
            deadline=time.monotonic()+15
            while g.busy and time.monotonic()<deadline:
                self.root.update(); time.sleep(.02)
            self.assertFalse(g.busy)
            error.assert_not_called()
        folder=Path(g.output_root.get())/Path(self.result['source']).stem
        manifest=json.loads((folder/'复核记录.json').read_text(encoding='utf8'))
        self.assertEqual(manifest['categories']['彩条']['selected_ids'],['5'])
        self.assertFalse(list(folder.glob('logo*.png')))


if __name__=='__main__':unittest.main()
