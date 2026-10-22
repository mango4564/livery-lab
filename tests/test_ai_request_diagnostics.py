"""Tuning and transport audit regressions; no external network calls."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib import error

import vision
from components import batch_inputs, refine_body_surfaces, recognize_groups
from scripts.inspect_ai_requests import markdown_report
from test_components import decision
from tests.test_body_surface_audit import scene


class RequestDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        png = b'\x89PNG\r\n\x1a\n'+b'\0'*8+(800).to_bytes(4, 'big')+(1140).to_bytes(4, 'big')
        self.image = self.path/'atlas.png'
        self.image.write_bytes(png)
        self.scene = dict(objects=[dict(id='G001', bbox=[0, 0, 10, 2])], component_mode=True,
                          request_trace_dir=str(self.path/'ai_requests'), request_stage='classification',
                          request_batch='01', group_metadata=[])
        self.config = dict(enabled=True, base_url='https://private.invalid/v1', model='qwen-test',
                           api_key='NEVER-LOG-THIS', max_output_tokens=8192, include_usage=True)
        self.opener = MagicMock()
        self.response = MagicMock()
        self.response.__enter__.return_value = self.response
        payload = dict(labels={'G001':'彩条'}, reason='完整图案', confidence=0.9)
        self.response.read.return_value = json.dumps(dict(model='qwen-test', usage={
            'prompt_tokens':5000, 'completion_tokens':200, 'total_tokens':5200,
            'completion_tokens_details':{'reasoning_tokens':0, 'secret':'DO-NOT-LOG'}}, choices=[
                dict(finish_reason='stop', message=dict(content=json.dumps(payload)))])).encode()
        patcher = patch.object(vision, '_network_opener', return_value=self.opener)
        patcher.start(); self.addCleanup(patcher.stop)

    def call(self):
        return vision.recognize(self.scene, self.image, self.image, self.config)

    def test_success_records_actual_parameters_and_usage_without_credentials(self):
        self.opener.open.return_value = self.response
        result = self.call()
        self.assertEqual(result['categories']['彩条'], ['G001'])
        log = vision.request_summary(self.path/'ai_requests')
        self.assertEqual((log['logical_requests'], log['http_attempts'], log['failed_requests']), (1, 1, 0))
        record = log['requests'][0]
        self.assertEqual(record['images'][0]['width'], 800)
        self.assertEqual(record['usage']['prompt_tokens'], 5000)
        self.assertEqual(record['parameters']['stream_options'], {'include_usage':True})
        self.assertEqual(record['parameters']['max_tokens'], 8192)
        self.assertNotIn('reasoning_effort', record['parameters'])
        serialized = json.dumps(log)
        for forbidden in ('NEVER-LOG-THIS', 'private.invalid', 'data:image', 'DO-NOT-LOG'):
            self.assertNotIn(forbidden, serialized)
        (self.path/'ai_requests.json').write_text(json.dumps(log), encoding='utf8')
        report = markdown_report([self.path])
        self.assertIn('5000/200', report)
        self.assertIn('800×1140', report)

    def test_retry_counts_http_attempts_not_new_logical_batches(self):
        failure = error.HTTPError('https://private.invalid',502,'secret',{},BytesIO(b'secret'))
        self.opener.open.side_effect = [failure, self.response]
        with patch.object(vision.time, 'sleep'):
            self.call()
        log = vision.request_summary(self.path/'ai_requests')
        self.assertEqual((log['logical_requests'], log['http_attempts'], log['retries']), (1, 2, 1))
        self.assertEqual([a['status'] for a in log['requests'][0]['attempts']], ['http_error','received'])

    def test_http_and_validation_failures_leave_readable_records(self):
        self.opener.open.side_effect = error.HTTPError('https://private.invalid',400,'secret',{},BytesIO(b'secret'))
        with self.assertRaises(vision.VisionError): self.call()
        log = vision.request_summary(self.path/'ai_requests')
        self.assertEqual(log['failed_requests'], 1)
        self.assertEqual(log['requests'][0]['attempts'][0]['http_status'], 400)
        self.assertNotIn('secret', json.dumps(log))
        self.opener.open.side_effect = None
        self.opener.open.return_value = self.response
        self.response.read.return_value = b'{"choices":[]}'
        with self.assertRaises(vision.VisionError): self.call()
        self.assertEqual(vision.request_summary(self.path/'ai_requests')['failed_requests'], 2)

    def test_inflight_request_is_pending_not_failed(self):
        directory=self.path/'pending'; directory.mkdir()
        (directory/'one.json').write_text(json.dumps(dict(stage='classification',status='running',attempts=[{}])))
        log=vision.request_summary(directory)
        self.assertEqual((log['pending_requests'],log['failed_requests']), (1,0))

    def test_sse_final_usage_chunk_is_preserved(self):
        events=[dict(model='qwen',choices=[dict(delta=dict(content='{}'),finish_reason=None)]),
                dict(choices=[dict(delta={},finish_reason='stop')]),
                dict(choices=[],usage=dict(prompt_tokens=12,completion_tokens=3,total_tokens=15))]
        raw=(''.join('data: '+json.dumps(e)+'\n\n' for e in events)+'data: [DONE]\n\n').encode()
        envelope=vision._response_envelope(raw)
        self.assertEqual(envelope['usage']['total_tokens'],15)
        self.assertEqual(envelope['model'],'qwen')


class TuningTests(unittest.TestCase):
    def test_model_defaults_and_invalid_fields(self):
        self.assertEqual(vision.component_batch_size({'model':'QWEN3.8'}),12)
        self.assertEqual(vision.component_batch_size({'model':'gemini'}),0)
        self.assertTrue(vision.compact_prompt({'model':'qwen'}))
        self.assertFalse(vision.compact_prompt({'model':'gemini'}))
        self.assertEqual(vision.component_batch_size({'model':'qwen','component_batch_size':0}),0)
        for config in ({'component_batch_size':121},{'component_batch_size':True},
                       {'max_output_tokens':-1},{'temperature':float('nan')},
                       {'top_p':0},{'reasoning_effort':'max'},{'prompt_style':'unknown'}):
            with self.subTest(config=config), self.assertRaises(vision.VisionError):
                vision.tuning_options(config)

    def test_labels_require_exact_coverage_and_restrict_audit_categories(self):
        base=dict(labels={'G001':'彩条'},reason='test',confidence=.9)
        self.assertEqual(vision._label_categories(base,{'G001'})['categories']['彩条'],['G001'])
        for labels in ({},{'G002':'彩条'},{'G001':'unknown'}):
            with self.subTest(labels=labels), self.assertRaises(vision.VisionError):
                vision._label_categories(dict(base,labels=labels),{'G001'})
        with self.assertRaises(vision.VisionError):
            vision._label_categories(dict(base,labels={'G001':'车身'}),{'G001'},surface_audit=True)

    def test_malformed_region_schema_is_repairable_but_truncated_response_is_not(self):
        payload=dict(labels={'G001':'车漆'},source_regions={},source_views={},reason='test',confidence=.9)
        def raw(finish):
            return json.dumps({'choices':[{'finish_reason':finish,'message':{'content':json.dumps(payload)}}]}).encode()
        scene=dict(component_mode=True,compact_labels=True,body_surface_audit=True)
        with self.assertRaises(vision.CompactLabelError):
            vision._decode_classification(raw('stop'),scene,{'G001'})
        with self.assertRaises(vision.VisionError) as raised:
            vision._decode_classification(raw('length'),scene,{'G001'})
        self.assertNotIsInstance(raised.exception,vision.CompactLabelError)

    def test_bad_labels_split_to_singleton_and_never_swallow_transport_errors(self):
        candidates=[dict(id=f'G{i:03}',bbox=[0,0,1,1],native_type='AGroup',source_span=[0,1],member_ids=[str(i)])
                    for i in range(4)]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'source_scene.png').write_bytes(b'preview')
            responses=[vision.CompactLabelError('missing'),decision(彩条=['G000']),decision(彩条=['G002'])]
            with patch('vision.recognize',side_effect=responses) as model,patch('components.candidate_atlas'):
                checked=recognize_groups(candidates,root,{'model':'qwen'},classification_batch=True)
            self.assertEqual(checked['categories']['彩条'],['G000','G002'])
            self.assertEqual([len(c.args[0]['objects']) for c in model.call_args_list],[4,2,2])
            with patch('vision.recognize',side_effect=vision.CompactLabelError('missing')) as model, \
                    patch('components.candidate_atlas'):
                with self.assertRaises(vision.CompactLabelError): recognize_groups(candidates,root,{'model':'qwen'})
            self.assertEqual(model.call_count,5)  # 4 -> 2 -> 1, then two bounded correction attempts.
            with patch('vision.recognize',side_effect=vision.VisionError('HTTP 400')) as model:
                with self.assertRaises(vision.VisionError): recognize_groups(candidates,root,{'model':'qwen'})
            model.assert_called_once()

    def test_second_protocol_failure_can_recover_without_losing_candidates(self):
        candidates=[dict(id=f'G{i:03}',bbox=[0,0,1,1],native_type='AGroup',
                         source_span=[0,1],member_ids=[str(i)]) for i in range(6)]
        batches=[]
        def classify(scene,*args):
            ids=[c['id'] for c in scene['objects']]
            batches.append((ids,scene['request_batch']))
            if len(ids)>2:
                raise vision.CompactLabelError('missing')
            return decision(车漆=ids)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'source_scene.png').write_bytes(b'preview')
            with patch('vision.recognize',side_effect=classify),patch('components.candidate_atlas'):
                checked=recognize_groups(candidates,root,{'model':'qwen'},surface_audit=True)
        self.assertEqual(checked['categories']['车漆'],[c['id'] for c in candidates])
        self.assertEqual([len(ids) for ids,_ in batches],[6,3,2,1,3,2,1])
        self.assertEqual(len({batch for _,batch in batches}),len(batches))

    def test_compact_atlas_uses_two_columns_and_skips_duplicate_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'source_scene.png').write_bytes(b'preview')
            with patch('components.candidate_atlas') as atlas, patch('components.detail_candidates') as details:
                batch_inputs([],root/'batch',root,{'model':'qwen'})
            self.assertEqual(atlas.call_args.kwargs['columns'],2)
            details.assert_not_called()

    def test_body_audit_batches_merge_all_explicit_labels(self):
        objects,candidates,first=scene()
        def classify(batch,*args,**kwargs):
            self.assertTrue(kwargs['surface_audit'])
            self.assertLessEqual(len(batch),3)
            return decision(彩条=[c['id'] for c in batch if c['member_ids']==['stripe']])
        with tempfile.TemporaryDirectory() as tmp, patch('components.recognize_groups',side_effect=classify) as model, \
                patch('components.rasterize'), patch('components.candidate_atlas'):
            # The real audit writer creates the residual overview; supply its bytes for batch copying.
            root=Path(tmp); (root/'source_scene.png').write_bytes(b'preview')
            audit=root/'body_surface_audit';audit.mkdir();(audit/'source_scene.png').write_bytes(b'preview')
            merged,coverage=refine_body_surfaces(objects,candidates,first,root,{'component_batch_size':3})
        self.assertEqual(model.call_count,3)
        self.assertEqual(coverage['body_surface_audit_classified'],1)
        self.assertEqual(len(merged['categories']['彩条']),1)


if __name__=='__main__': unittest.main()
