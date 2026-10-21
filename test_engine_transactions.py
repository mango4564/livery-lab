"""Offline transaction/cache tests: python -m unittest test_engine_transactions -v."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from PIL import Image
import engine


class EngineTransactions(unittest.TestCase):
    def test_legacy_reference_options_are_ignored_without_lookup(self):
        with patch.object(engine, 'read_config', side_effect=AssertionError('reference config lookup')):
            self.assertEqual(engine._resolve_reference_directory(self.source,
                             {'reference_directory': 'must-not-be-opened'}), '')

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/'sample.fs'
        self.source.write_bytes(b'offline simulated FS source')
        self.output=self.root/'out'
        self.calls=0
        self.fingerprint=patch.object(engine,'_fingerprint',return_value='offline-pipeline')
        self.fingerprint.start()
        self.addCleanup(self.fingerprint.stop)
        self.worker=patch.object(engine.subprocess,'run',side_effect=self.fake_worker)
        self.worker.start()
        self.addCleanup(self.worker.stop)
        # Any accidental AI call fails immediately rather than reaching a service.
        self.network=patch('vision.request.build_opener',side_effect=AssertionError('No network in offline tests'))
        self.network.start()
        self.addCleanup(self.network.stop)

    @staticmethod
    def png(path,colour='blue'):
        Image.new('RGBA',(12,8),colour).save(path)

    def fake_worker(self,cmd,**kwargs):
        self.calls+=1
        marker=cmd.index('_worker')
        source,directory,digest,fingerprint,use_ai=cmd[marker+1:]
        directory=Path(directory)
        run=directory.parent.parent
        key=engine._source_key(source,digest,fingerprint)
        result={'source':source,'source_sha256':digest,'pipeline_fingerprint':fingerprint,
                'run_dir':str(run),'output_dir':str(directory),
                'cache_index':str(run.parent/'.cache'/(key+'.json')),
                'status':'review','method':'offline_mock', 'warnings':[],
                'human_confirmed':False,'physical_scale_verified':False,
                'objects':[{'id':'a','d':'M0 0 L10 0 L10 3 Z','bbox':[0,0,10,3],'fill':'#0000ff'},
                           {'id':'b','d':'M1 1 L9 1 L9 4 Z','bbox':[1,1,9,4],'fill':'#ff0000'}],
                'selected_ids':['a'],'views':[]}
        for key,name in (('preview_png','preview.png'),('candidate_png','candidates.png'),('result_png','result.png')):
            self.png(directory/name)
            result[key]=str(directory/name)
        result['result_svg']=str(directory/'result.svg')
        (directory/'result.svg').write_text(f'<svg>mock run {self.calls}</svg>',encoding='utf8')
        engine._seal_version(result,directory)
        return types.SimpleNamespace(returncode=0)

    def process(self,**options):
        return engine.process_file(self.source,self.output,options)

    def snapshot(self,result):
        paths=engine._artifact_paths(result)+[Path(result['version_manifest']),Path(result['run_dir'])/'manifest.json',Path(result['cache_index'])]
        return {str(p):p.read_bytes() for p in paths}

    def test_png_failure_keeps_previous_outputs_pointer_and_cache(self):
        original=self.process()
        before=self.snapshot(original)
        with patch.object(engine,'rasterize',side_effect=RuntimeError('simulated PNG renderer failure')):
            with self.assertRaisesRegex(RuntimeError,'simulated PNG'):
                engine.export_selection(original,['b'])
        self.assertEqual(before,self.snapshot(original))
        cached=self.process()
        self.assertEqual(cached['selected_ids'],['a'])
        self.assertEqual(cached['output_dir'],original['output_dir'])
        self.assertEqual(self.calls,1)

    def test_force_updates_latest_cache_and_keeps_prior_version(self):
        first=self.process()
        first_snapshot=self.snapshot(first)
        forced=self.process(force=True)
        cached=self.process()
        self.assertNotEqual(first['output_dir'],forced['output_dir'])
        self.assertEqual(cached['output_dir'],forced['output_dir'])
        for path,content in first_snapshot.items():
            if path!=first['cache_index']:
                self.assertEqual(Path(path).read_bytes(),content)
        self.assertEqual(self.calls,2)

    def test_review_is_immutable_new_version_and_cache_reuses_it(self):
        original=self.process()
        immutable={str(p):p.read_bytes() for p in engine._artifact_paths(original)+[Path(original['version_manifest'])]}
        with patch.object(engine,'rasterize',side_effect=lambda svg,path,**kwargs:self.png(path,'red')):
            revised=engine.export_selection(original,['b'])
        self.assertNotEqual(original['output_dir'],revised['output_dir'])
        self.assertEqual(revised['run_dir'],original['run_dir'])
        self.assertTrue(revised['human_confirmed'])
        self.assertEqual(revised['selected_ids'],['b'])
        cached=self.process()
        self.assertEqual(cached['selected_ids'],['b'])
        self.assertEqual(cached['output_dir'],revised['output_dir'])
        self.assertEqual(self.calls,1)
        for path,content in immutable.items():
            self.assertEqual(Path(path).read_bytes(),content)
        self.assertTrue(Path(revised['preview_png']).is_relative_to(Path(revised['output_dir'])))

    def test_component_review_keeps_other_categories_and_version_integrity(self):
        original=self.process()
        original.update(extraction_mode='components', component_categories={'车身':['a','b'],'彩条':[],
                        '文字':[],'logo':[],'门徽':[]})
        original['views']=[{'category':'车身','label':'body','selected_ids':['a','b'],
                            'bbox':[0,0,10,4],'svg':original['result_svg'],'png':original['result_png']},
                           {'category':'彩条','label':'stripe','selected_ids':[],
                            'bbox':[0,0,0,0],'svg':original['result_svg'],'png':original['result_png']}]
        with patch.object(engine,'rasterize',side_effect=lambda svg,path,**kwargs:self.png(path,'red')):
            revised=engine.export_selection(original,['b'],category_name='彩条')
        self.assertEqual(revised['component_categories']['车身'],['a'])
        self.assertEqual(revised['component_categories']['彩条'],['b'])
        self.assertEqual(set(revised['selected_ids']),{'a','b'})
        self.assertEqual(revised['status'],'review')
        self.assertEqual(revised['confirmed_categories'],['彩条'])
        self.assertEqual(self.process()['component_categories'],revised['component_categories'])

    def test_invalid_ids_do_not_create_a_version_or_change_manifest(self):
        original=self.process()
        before=self.snapshot(original)
        versions=list((Path(original['run_dir'])/'versions').iterdir())
        for invalid in ([],['missing'],['a','a']):
            with self.assertRaises(ValueError):
                engine.export_selection(original,invalid)
        self.assertEqual(versions,list((Path(original['run_dir'])/'versions').iterdir()))
        self.assertEqual(before,self.snapshot(original))

    def test_tampered_artifact_or_manifest_cannot_hit_cache(self):
        original=self.process()
        Path(original['result_png']).write_bytes(b'tampered PNG')
        replacement=self.process()
        self.assertNotEqual(original['output_dir'],replacement['output_dir'])
        path=Path(replacement['version_manifest'])
        content=json.loads(path.read_text(encoding='utf8'))
        content['selected_ids']=['b']
        path.write_text(json.dumps(content),encoding='utf8')
        third=self.process()
        self.assertNotEqual(replacement['output_dir'],third['output_dir'])
        self.assertEqual(self.calls,3)

    def test_same_name_and_content_at_different_source_paths_have_separate_cache(self):
        first=self.process()
        another=self.root/'other'/'sample.fs'
        another.parent.mkdir()
        another.write_bytes(self.source.read_bytes())
        second=engine.process_file(another,self.output)
        self.assertNotEqual(first['cache_index'],second['cache_index'])
        self.assertEqual(Path(second['source']),another)
        self.assertEqual(self.process()['source'],str(self.source.resolve()))
        self.assertEqual(self.calls,2)

    def test_changed_pipeline_is_not_published(self):
        with patch.object(engine,'_fingerprint',side_effect=['before','after']):
            with self.assertRaisesRegex(RuntimeError,'代码或 AI 配置发生变化'):
                self.process()
        self.assertFalse(list(self.output.glob('.cache/*.json')))
        self.assertFalse(list(self.output.glob('*/manifest.json')))

    def test_worker_checks_pipeline_before_reading_fs(self):
        with patch.object(engine,'extract_geometry') as extract:
            with self.assertRaisesRegex(RuntimeError,'代码或 AI 配置发生变化'):
                engine._run_worker(self.source,self.root/'not-created','digest','different-fingerprint',False)
            extract.assert_not_called()


if __name__=='__main__':
    unittest.main()
