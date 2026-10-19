"""Final naming, review gates and transactional publication regressions."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from reviewed_exports import LABELS, export_reviewed


def fixture(root):
    source = root/'乾途示例 3261754.fs'
    source.write_bytes(b'review-export-test-source')
    objects = [{'id':str(i), 'd':f'M{i*20} 0 L{i*20+10} 0 L{i*20+10} 8 Z',
                'bbox':[i*20,0,i*20+10,8], 'fill':'#167ce2'} for i in range(6)]
    return {'source':str(source), 'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'objects':objects,'component_categories':{name:([str(i)] if name != '车漆' else None) for i,name in enumerate(LABELS)}}


class ReviewedExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.result=fixture(self.root)
        self.choices=dict(self.result['component_categories'])
        self.output=self.root/'成品'

    def test_exact_source_folder_and_five_named_transparent_pairs(self):
        published=export_reviewed(self.result,self.choices,self.output)
        folder=Path(published['output_dir'])
        stem=Path(self.result['source']).stem
        self.assertEqual(folder,self.output/stem)
        names={label+'_'+stem+suffix for name,label in LABELS.items() if self.choices[name] for suffix in ('.svg','.png')}
        self.assertEqual({p.name for p in folder.iterdir()},names|{'复核记录.json'})
        for p in folder.glob('*.png'):
            with Image.open(p) as im:
                self.assertEqual(im.mode,'RGBA')
                self.assertEqual(im.getextrema()[3][0],0)
                self.assertEqual(im.getextrema()[3][1],255)

    def test_missing_confirmation_never_creates_products(self):
        del self.choices['logo']
        with self.assertRaisesRegex(ValueError,'逐类确认'):
            export_reviewed(self.result,self.choices,self.output)
        self.assertFalse(self.output.exists())

    def test_explicit_absence_has_no_empty_files(self):
        self.choices['logo']=None
        p=export_reviewed(self.result,self.choices,self.output)
        self.assertFalse(list(Path(p['output_dir']).glob('logo*')))
        self.assertEqual(p['manifest']['categories']['logo']['status'],'confirmed_absent')

    def test_failed_png_keeps_previous_final_files_and_cleans_staging(self):
        first=export_reviewed(self.result,self.choices,self.output)
        folder=Path(first['output_dir']); before={p.name:p.read_bytes() for p in folder.iterdir()}
        self.choices['彩条']=['5']
        with patch('reviewed_exports.rasterize',side_effect=RuntimeError('PNG failed')):
            with self.assertRaises(RuntimeError):export_reviewed(self.result,self.choices,self.output)
        self.assertEqual(before,{p.name:p.read_bytes() for p in folder.iterdir()})
        self.assertFalse(list(self.output.glob('.review_stage_*')))

    def test_reexport_archives_old_and_removes_now_absent_pair(self):
        first=export_reviewed(self.result,self.choices,self.output)
        self.choices['logo']=None
        self.choices['彩条']=['5']
        second=export_reviewed(self.result,self.choices,self.output)
        self.assertEqual(first['output_dir'],second['output_dir'])
        self.assertTrue(list(Path(second['previous_version']).glob('logo*.svg')))
        self.assertFalse(list(Path(second['output_dir']).glob('logo*.svg')))
        self.assertEqual(second['manifest']['categories']['彩条']['selected_ids'],['5'])

    def test_same_name_different_source_cannot_merge(self):
        export_reviewed(self.result,self.choices,self.output)
        second=self.root/'other';second.mkdir()
        result=fixture(second)
        with self.assertRaisesRegex(ValueError,'另一份同名'):
            export_reviewed(result,self.choices,self.output)

    def test_changed_source_or_unknown_ids_are_rejected(self):
        invalid=dict(self.choices,logo=['unknown'])
        with self.assertRaisesRegex(ValueError,'未知对象'):
            export_reviewed(self.result,invalid,self.output)
        Path(self.result['source']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'源 FS 已变化'):
            export_reviewed(self.result,self.choices,self.output)

    def test_body_excludes_subobjects_but_other_overlap_is_rejected(self):
        self.choices['车身']=['0','1']
        result=export_reviewed(self.result,self.choices,self.output)
        self.assertEqual(result['manifest']['categories']['车身']['selected_ids'],['0'])
        self.choices['logo']=['1']
        with self.assertRaisesRegex(ValueError,'重叠对象'):
            export_reviewed(self.result,self.choices,self.output)


if __name__=='__main__':unittest.main()
