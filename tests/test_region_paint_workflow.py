"""Region evidence and reversible paint corrections; no live AI calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from components import (batch_inputs, candidate_region, context_regions,
                        refine_body_surfaces, region_batches, recognize_groups,
                        residual_surface_candidates, selected_body_anchors,
                        refine_paint_consistency)
from test_components import decision
from tests.test_body_surface_audit import scene
from vision import recognize


class RegionPaintTests(unittest.TestCase):
    def test_matching_fill_only_triggers_review_and_never_auto_selects_lamp(self):
        objects,candidates,first=scene()
        corner=dict(id='corner',source_offset=10000,bbox=[50,45,70,50],fill='#cc0000',
                    details={'closed':True},d='M50 45H70V50H50Z')
        objects.append(corner)
        candidates[0]['member_ids'].append('corner');candidates[0]['members'].append(corner)
        for gid,obj in (('G005',objects[1]),('G006',objects[3]),('G007',corner)):
            b=obj['bbox'];candidates.append(dict(id=gid,native_type='PaintSurface',
                member_ids=[obj['id']],members=[obj],bbox=b,source_span=[obj['source_offset']]*2,
                area=(b[2]-b[0])*(b[3]-b[1])))
        first['categories']['车漆'].append('G005')
        before=deepcopy(first);seen=[]
        def classify(batch,folder,config,**kwargs):
            self.assertEqual(len(batch),1);self.assertTrue(config['_paint_consistency'])
            seen.append(batch[0]['member_ids'][0])
            return decision(车漆=[batch[0]['id']] if batch[0]['member_ids']==['corner'] else [])
        with tempfile.TemporaryDirectory() as tmp, \
                patch('components.batch_inputs',side_effect=lambda batch,folder,*args:folder.mkdir(parents=True,exist_ok=True)), \
                patch('components.recognize_groups',side_effect=classify):
            merged,coverage=refine_paint_consistency(objects,candidates,first,Path(tmp),{})
        self.assertEqual(set(seen),{'lamp','corner'})
        self.assertEqual(coverage,dict(paint_consistency_candidates=2,paint_consistency_added=1))
        self.assertEqual(first,before)
        from components import resolve_categories
        result=resolve_categories(objects,candidates,merged)
        self.assertIn('corner',result['车漆']);self.assertIn('lamp',result['车身'])

    def test_local_roof_mislabelled_as_body_uses_complete_vehicle_context(self):
        objects,candidates,first=scene()
        roof=dict(id='G005',member_ids=['roof'],members=[objects[1]],
                  bbox=objects[1]['bbox'],area=100,aspect=25,native_type='SourcePath')
        candidates.append(roof)
        first['categories']['车身'].append('G005')
        self.assertEqual({c['id'] for c in selected_body_anchors(candidates,first)}, {'G001','G002'})
        audit,_=residual_surface_candidates(objects,candidates,first)
        target=next(c for c in audit if c['member_ids']==['roof'])
        self.assertEqual(target['body_anchor_id'],'G001')

    def test_surface_detail_contains_actual_source_removal_at_equal_scale(self):
        from components import render_region_context
        shell=dict(id='shell',source_offset=0,bbox=[0,0,100,50],fill='#ffffff',d='M0 0H100V50H0Z')
        roof=dict(id='roof',source_offset=1,bbox=[20,40,70,50],fill='#cc0000',d='M20 40H70V50H20Z')
        seam=dict(id='seam',source_offset=2,bbox=[40,40,41,50],fill='#000000',d='M40 40H41V50H40Z')
        anchor=dict(id='G001',bbox=[0,0,100,50],area=5000)
        target=dict(id='G002',member_ids=['roof'],bbox=roof['bbox'],body_anchor_id='G001')
        with tempfile.TemporaryDirectory() as tmp:
            render_region_context([target],Path(tmp),dict(_context_objects=[shell,roof,seam],
                _context_anchors=[anchor],_paint_completeness=True))
            with Image.open(Path(tmp)/'surface_details.png') as im:
                self.assertEqual(im.size,(1260,180))
                # Original detail keeps the black seam; target-only contains
                # just the red fill; removal leaves the shell and seam.
                colors=[{color for _,color in im.crop((i*420,30,(i+1)*420,180)).getcolors(420*150)}
                        for i in range(3)]
                self.assertIn((204,0,0),colors[0]);self.assertIn((0,0,0),colors[0])
                self.assertIn((204,0,0),colors[1]);self.assertNotIn((0,0,0),colors[1])
                self.assertNotIn((204,0,0),colors[2]);self.assertIn((0,0,0),colors[2])

    def test_full_regional_audit_reaches_roof_after_120_surfaces(self):
        objects,candidates,first=scene()
        body=candidates[0]
        # Many eligible faces of the same colour precede a smaller roof.
        for i in range(140):
            obj=dict(id=f'panel-{i}',source_offset=10000+i,bbox=[1,1,21,21],
                     fill='#cc0000',details={'closed':True},d='M1 1H21V21H1Z')
            objects.append(obj)
            body['member_ids'].append(obj['id'])
            body['members'].append(obj)
        seen=[]
        def classify(batch,*args,**kwargs):
            self.assertLessEqual(len(batch),12)
            seen.extend(c['member_ids'][0] for c in batch)
            return decision(车漆=[c['id'] for c in batch if c['member_ids'] in (['base'],['roof'])])
        with tempfile.TemporaryDirectory() as tmp, \
                patch('components.batch_inputs',side_effect=lambda batch,folder,*args:folder.mkdir(parents=True,exist_ok=True)), \
                patch('components.rasterize'),patch('components.candidate_atlas'), \
                patch('components.recognize_groups',side_effect=classify):
            merged,coverage=refine_body_surfaces(objects,candidates,first,Path(tmp),
                {'_region_workflow':True,'component_batch_size':12})
        self.assertGreater(seen.index('roof'),120)
        self.assertIn('base',seen)
        self.assertEqual(len(seen),len(set(seen)))
        self.assertEqual(coverage['body_surface_audit_omitted'],0)
        self.assertEqual(coverage['body_surface_audit_eligible'],147)
        from components import resolve_categories
        resolved=resolve_categories(objects,candidates,merged)
        self.assertIn('roof',resolved['车漆'])
        self.assertNotIn('roof',resolved['车身'])
        self.assertIn('glass',resolved['车身'])

    def test_end_view_pairs_with_separate_base_and_all_ids_survive_once(self):
        objects, candidates, _ = scene()
        side, rear = candidates[:2]
        front = dict(rear, id='G010', bbox=[400, 0, 415, 25], area=375)
        base = dict(candidates[2], id='G074', bbox=[400, 1, 415.01, 24], area=345)
        pool = [side, rear, front, candidates[2], candidates[3], base]
        before = deepcopy(pool)
        batches = region_batches(pool, 3, [side, rear, front])
        self.assertTrue(any({'G010', 'G074'} <= {c['id'] for c in batch} for batch in batches))
        self.assertCountEqual([c['id'] for b in batches for c in b], [c['id'] for c in pool])
        self.assertEqual(pool, before)
        for batch in batches:
            owners = {a['id'] for c in batch if (a := candidate_region(c, [side, rear, front]))}
            self.assertLessEqual(len(owners), 1)
            self.assertLessEqual(len(batch), 3)

    def test_page_parent_is_not_used_to_merge_two_vehicle_crops(self):
        def group(gid, box):
            w, h = box[2]-box[0], box[3]-box[1]
            return dict(id=gid, bbox=box, area=w*h, aspect=w/h,
                        native_type='AGroup', member_ids=list(map(str, range(80))))
        a, b = group('G001', [0, 0, 100, 50]), group('G002', [120, 0, 220, 50])
        page = group('G003', [0, 0, 220, 50])
        self.assertEqual({c['id'] for c in context_regions([page, a, b])}, {'G001', 'G002'})

    def test_actual_context_image_keeps_original_and_locator_at_same_scale(self):
        objects, candidates, _ = scene()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Image.new('RGB', (20, 20), 'white').save(root/'source_scene.png')
            config = dict(model='qwen', _context_objects=objects, _context_anchors=candidates[:2],
                          _context_paint_ids=['base'], _paint_completeness=True)
            with patch('components.candidate_atlas'):
                batch_inputs([candidates[2]], root/'batch', root, config)
            info = json.loads((root/'batch/region_context.json').read_text(encoding='utf8'))
            self.assertEqual(info[0]['anchor_id'], 'G001')
            self.assertEqual(info[0]['candidate_ids'], ['G003'])
            self.assertLess(info[0]['bbox'][2], 200)  # Does not include the second vehicle.
            with Image.open(root/'batch/group_details.png') as im:
                self.assertEqual(im.size, (1680, 388))
                self.assertNotEqual(im.crop((0, 30, 560, 340)).tobytes(),
                                    im.crop((560, 30, 1120, 340)).tobytes())
            self.assertTrue((root/'batch/surface_details.png').exists())

    def test_regional_review_can_remove_wrong_paint_and_add_small_shell(self):
        objects, candidates, first = scene()
        # Initial model incorrectly selected the glass as paint.
        glass = dict(candidates[2], id='G005', member_ids=['glass'],
                     members=[objects[2]], bbox=objects[2]['bbox'])
        candidates.append(glass)
        first['categories']['车漆'] = ['G003', 'G005']
        before = deepcopy(first)
        reviewed = []
        def classify(batch, directory, config, **kwargs):
            self.assertTrue(kwargs['surface_audit'])
            self.assertTrue(config['_paint_completeness'])
            reviewed.extend(c['id'] for c in batch if c.get('recheck_paint'))
            return decision(车漆=[c['id'] for c in batch if c['member_ids'] in (['base'], ['roof'])])
        with tempfile.TemporaryDirectory() as tmp, patch('components.batch_inputs', side_effect=lambda batch, folder, *args: folder.mkdir(parents=True, exist_ok=True)), \
                patch('components.rasterize'), patch('components.candidate_atlas'), \
                patch('components.recognize_groups', side_effect=classify):
            merged, coverage = refine_body_surfaces(objects, candidates, first, Path(tmp),
                                                    {'_region_workflow': True, 'component_batch_size': 3})
        self.assertEqual(set(reviewed), {'G003', 'G005'})
        self.assertEqual(first, before)
        self.assertNotIn('G005', merged['categories']['车漆'])
        self.assertIn('G003', merged['categories']['车漆'])
        self.assertEqual(coverage['paint_completeness_removed'], 1)
        from components import resolve_categories
        resolved = resolve_categories(objects, candidates, merged)
        self.assertIn('roof', resolved['车漆'])
        self.assertIn('glass', resolved['车身'])
        self.assertEqual(len({c['id'] for c in candidates}), len(candidates))

    def test_failed_review_never_changes_existing_paint_or_candidate_pool(self):
        objects, candidates, first = scene()
        before, original = deepcopy(candidates), deepcopy(first)
        from vision import VisionError
        with tempfile.TemporaryDirectory() as tmp, patch('components.batch_inputs', side_effect=lambda batch, folder, *args: folder.mkdir(parents=True, exist_ok=True)), \
                patch('components.rasterize'), patch('components.candidate_atlas'), \
                patch('components.recognize_groups', side_effect=VisionError('offline')):
            with self.assertRaises(VisionError):
                refine_body_surfaces(objects, candidates, first, Path(tmp), {'_region_workflow': True})
        self.assertEqual(candidates, before)
        self.assertEqual(first, original)

    def test_metadata_contains_global_aliases_and_previous_paint(self):
        objects, candidates, _ = scene()
        c = dict(candidates[2], source_span=[0, 1], recheck_paint=True)
        alias = dict(c, id='G099')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'region_context.json').write_text('[{"anchor_id":"G001","candidate_ids":["G003"]}]')
            with patch('vision.recognize', return_value=decision()) as model:
                recognize_groups([c], root, dict(_region_workflow=True, _paint_completeness=True,
                                 _all_candidates=[c, alias], _context_anchors=candidates[:2]), surface_audit=True)
            sent = model.call_args.args[0]
            self.assertTrue(sent['paint_completeness'])
            meta = sent['group_metadata'][0]
            self.assertEqual(meta['shared_source_candidates'], ['G099'])
            self.assertEqual(meta['previous_category'], '车漆')
            self.assertEqual(meta['context_anchor'], 'G001')

    def test_regional_prompt_accepts_explicit_rejection_and_explains_panels(self):
        for model in ('qwen-test', 'gemini-test'):
            payload = (dict(labels={'G003': '不提取'}, reason='玻璃', confidence=.9)
                       if model.startswith('qwen') else decision())
            response = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {
                'content': json.dumps(payload)}}]}).encode()
            with self.subTest(model=model), patch('vision._image_part', return_value={'type': 'image_url'}), \
                    patch('vision._request_bytes', return_value=response) as send:
                result = recognize(dict(objects=[dict(id='G003', bbox=[0, 0, 10, 10])],
                    component_mode=True, body_surface_audit=True, paint_completeness=True,
                    region_context=[{'anchor_id':'G001'}], group_metadata=[],
                    candidate_sheet_png='context.png',surface_detail_png='detail.png'), 'original.png', 'atlas.png',
                    dict(enabled=True, base_url='https://example.invalid/v1', model=model, api_key='test'))
                prompt = json.loads(send.call_args.args[0].data)['messages'][1]['content'][0]['text']
                self.assertIn('CURRENT PAINT', prompt)
                self.assertIn('撤回原车漆标签', prompt)
                self.assertIn('小面、单条路径、渐变', prompt)
                self.assertIn('车顶空调罩',prompt)
                self.assertIn('与侧面渐变主底色不是同一源对象',prompt)
                self.assertIn('ORIGINAL WITHOUT TARGET',prompt)
                self.assertEqual(len(json.loads(send.call_args.args[0].data)['messages'][1]['content']),5)
                self.assertFalse(any(result['categories'].values()))


if __name__ == '__main__':
    unittest.main()
