"""Offline semantic gate: source-only edits, local rollback and honest coverage."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from components import CATEGORIES, build_candidates, resolve_categories
from core.candidate_audit import (apply_patches, audit_components, audit_options,
                                  audit_pool, audit_regions, body_paint_transfer, render_evidence, validate_audit)
from test_components import decision
from vision import recognize, read_config, VisionError
from vision import _read_with_deadline


def fixture():
    objects = []
    for i, (name, box, color) in enumerate((
            ('base', [0, 0, 100, 50], '#ffffff'),
            ('glass', [10, 20, 80, 40], '#111111'),
            ('blue', [0, 10, 70, 15], '#0044bb'),
            ('wide', [-2, 15, 95, 22], '#55bbff'),
            ('mark', [80, 10, 90, 20], '#00bb44'),
            ('text', [15, 2, 60, 7], '#111111'),
            ('phone', [60, 2, 90, 7], '#111111'))):
        a, b, c, d = box
        objects.append(dict(id=name, source_offset=i*10, bbox=box, fill=color,
                            d=f'M{a} {b}H{c}V{d}H{a}Z'))
    candidates = []
    for i, obj in enumerate(objects, 1):
        candidates.append(dict(id=f'G{i:03d}', native_type='SourcePath', members=[obj],
                               member_ids=[obj['id']], bbox=obj['bbox'],
                               source_span=[i*10, i*10], area=100, aspect=1))
    # The native body parent intentionally overlaps separately classified art.
    candidates.append(dict(id='G100', native_type='AGroup', members=list(objects),
                           member_ids=[o['id'] for o in objects], bbox=[-2, 0, 100, 50],
                           source_span=[0, 100], area=5100, aspect=2))
    first = decision(车身=['G100'], 车漆=['G001'], 彩条=['G003', 'G002'], 文字=['G006', 'G007'])
    return objects, candidates, first


def passed(source='not_assessed'):
    return dict(verdict='pass', issues=[], patches=[], source_check=source)


def repair(category='彩条', add=('G004',), remove=('G002',)):
    return dict(verdict='repair', source_check='not_assessed',
                issues=[dict(category=category, kind='incomplete', candidate_ids=list(add)+list(remove),
                             evidence='浅蓝宽曲线遗漏，黑色玻璃误入。')],
                patches=[dict(category=category, add_ids=list(add), remove_ids=list(remove))])


class CandidateAuditTests(unittest.TestCase):
    def paint_fixture(self):
        objects, candidates, first = fixture()
        # A coloured roof with two independent black/grey wireframe paths.
        objects[3].update(fill='none', stroke='#000000')
        roof = dict(candidates[2], id='G200', native_type='AGroup',
                    members=[objects[2], objects[3]], member_ids=['blue', 'wide'])
        candidates.append(roof)
        first['categories']['彩条'] = []
        move = dict(verdict='repair', source_check='not_assessed',
                    issues=[dict(category=n, kind='wrong_category', candidate_ids=['G200'],
                                 evidence='蓝色车顶外壳漆面残留在车身，应转入车漆。') for n in ('车身','车漆')],
                    patches=[dict(category='车身', add_ids=[], remove_ids=['G200']),
                             dict(category='车漆', add_ids=['G200'], remove_ids=[])])
        return objects, candidates, first, move

    def test_body_paint_move_preserves_source_outline_and_geometry(self):
        objects, candidates, first, response = self.paint_fixture()
        before = deepcopy((objects, candidates, first))
        proposal, move = body_paint_transfer(objects, candidates, first, response['patches'],
                                              {'G200'}, {o['id'] for o in objects})
        staged, extra = apply_patches(objects, candidates+[proposal], first, move,
                                      {proposal['id']}, {o['id'] for o in objects})
        selected = resolve_categories(objects,candidates+[proposal]+extra,staged)
        self.assertIn('blue', selected['车漆'])
        self.assertNotIn('blue', selected['车身'])
        self.assertIn('wide', selected['车身'])
        self.assertIn('glass', selected['车身'])
        self.assertNotIn('wide', selected['车漆'])
        self.assertEqual((objects,candidates,first),before)
        with self.assertRaises(ValueError):
            apply_patches(objects,candidates,first,response['patches'],{'G200'},
                          {o['id'] for o in objects})

    def paint_gate(self, replies, config=None):
        objects,candidates,first,move=self.paint_fixture()
        with tempfile.TemporaryDirectory() as directory, \
                patch('core.candidate_audit.independent_preview',return_value={'status':'unavailable'}), \
                patch('core.candidate_audit.inspect_page',side_effect=[move]+replies) as inspect:
            updated,report=audit_components('source.fs',objects,candidates,first,directory,config or {})
        return objects,candidates,first,updated,report,inspect

    def test_verified_paint_transfer_survives_other_category_failure(self):
        objects,candidates,first,updated,report,model=self.paint_gate([passed(),VisionError('later failure')])
        selected=resolve_categories(objects,candidates,updated)
        self.assertIn('blue',selected['车漆'])
        self.assertNotIn('blue',selected['车身'])
        self.assertIn('wide',selected['车身'])
        self.assertEqual(report['accepted_repairs'],1)
        self.assertEqual(report['body_paint_transfers'][0]['status'],'passed')
        self.assertEqual(report['status'],'review')
        self.assertTrue(model.call_args_list[1].args[6]['_body_paint_check'])

    def test_unverified_paint_move_is_rolled_back_on_rejection_failure_or_budget(self):
        rejected=dict(verdict='review',patches=[],source_check='not_assessed',issues=[
            dict(category='车漆',kind='wrong_category',candidate_ids=[],evidence='这是车窗玻璃。')])
        for replies,config in (([rejected],{}),([VisionError('failed')],{}),([],{ 'audit_max_requests':1})):
            with self.subTest(replies=replies):
                _,_,first,updated,report,_=self.paint_gate(replies,config)
                self.assertEqual(first,updated)
                self.assertEqual(report['body_paint_transfers'],[])
                self.assertEqual(report['accepted_repairs'],0)

    def test_verified_paint_move_survives_later_budget_and_is_not_double_counted(self):
        for replies,config in (([passed()],{'audit_max_requests':2}),([passed(),passed()],{})):
            objects,candidates,_,updated,report,_=self.paint_gate(replies,config)
            self.assertIn('blue',resolve_categories(objects,candidates,updated)['车漆'])
            self.assertEqual(report['accepted_repairs'],1)

    def test_protocol_rejects_fabricated_ids_and_false_passes(self):
        for payload in (repair(add=('invented',)), dict(passed(), issues=repair()['issues']),
                        dict(repair(), verdict='pass'), dict(repair(), patches=[]),
                        dict(passed(), verdict=[]),
                        dict(repair(), issues=[dict(repair()['issues'][0], kind=[])]),
                        dict(passed(), source_check='mismatch')):
            with self.subTest(payload=payload), self.assertRaises(VisionError):
                validate_audit(payload, {'G002', 'G004'})

    def test_source_patch_removes_glass_and_recovers_wide_curve_without_changing_geometry(self):
        objects, candidates, first = fixture()
        before = deepcopy((objects, candidates, first))
        staged, extra = apply_patches(objects, candidates, first, repair()['patches'],
                                      {'G002', 'G004'}, {o['id'] for o in objects})
        resolved = resolve_categories(objects, candidates+extra, staged)
        self.assertEqual(set(resolved['彩条']), {'blue', 'wide'})
        self.assertIn('glass', resolved['车身'])
        self.assertEqual((objects, candidates, first), before)

    def test_atomic_category_move_and_logo_emblem_sharing(self):
        objects, candidates, first = fixture()
        first['categories']['文字'].append('G005')
        patches = [dict(category='文字', add_ids=[], remove_ids=['G005']),
                   dict(category='门徽', add_ids=['G005', 'G006', 'G007'], remove_ids=[]),
                   dict(category='logo', add_ids=['G005'], remove_ids=[])]
        # Move all text into the emblem too; ordinary text and emblem may not overlap.
        patches[0]['remove_ids'] += ['G006', 'G007']
        staged, extra = apply_patches(objects, candidates, first, patches,
                                      {'G005', 'G006', 'G007'}, {o['id'] for o in objects})
        resolved = resolve_categories(objects, candidates+extra, staged)
        self.assertEqual(resolved['logo'], ['mark'])
        self.assertEqual(set(resolved['门徽']), {'mark', 'text', 'phone'})
        self.assertEqual(resolved['文字'], [])

    def test_patch_cannot_remove_unowned_members_or_touch_other_regions(self):
        objects, candidates, first = fixture()
        for patches, allowed, scope in (
                (repair(remove=('G005',))['patches'], {'G004', 'G005'}, {o['id'] for o in objects}),
                (repair()['patches'], {'G004'}, {o['id'] for o in objects}),
                (repair()['patches'], {'G004', 'G002'}, {'wide'})):
            with self.assertRaises(ValueError):
                apply_patches(objects, candidates, first, patches, allowed, scope)

    def test_emblem_repair_keeps_phone_outside_the_old_arc_and_badge_proposal(self):
        objects, candidates, first = fixture()
        candidates[5]['native_type'] = 'AArcText'
        first['categories']['文字'] = []
        first['categories']['门徽'] = ['G006']
        staged, extra = apply_patches(objects, candidates, first,
                                      [dict(category='门徽', add_ids=['G007'], remove_ids=[])],
                                      {'G007'}, {o['id'] for o in objects})
        self.assertEqual(set(resolve_categories(objects, candidates+extra, staged)['门徽']), {'text', 'phone'})

    def test_repair_keeps_independent_logos_separate_in_the_review_gallery(self):
        from components import review_candidates
        from review_gallery import classified_patterns
        objects, candidates, first = fixture()
        first['categories']['文字'] = []
        first['categories']['logo'] = ['G005', 'G006']
        staged, extra = apply_patches(objects, candidates, first,
                                      [dict(category='logo', add_ids=['G007'], remove_ids=[])],
                                      {'G007'}, {o['id'] for o in objects})
        ids = resolve_categories(objects,candidates+extra,staged)['logo']
        groups = classified_patterns('logo', ids, review_candidates(candidates+extra,staged))
        self.assertEqual({tuple(g) for g in groups}, {('mark',), ('text',), ('phone',)})

    def gate(self, responses, config=None, reference='unavailable'):
        objects, candidates, first = fixture()
        snapshots = []
        def inspect(objs, proposals, staged, scope, page, path, settings, ref):
            snapshots.append(resolve_categories(objs, proposals, staged))
            value = responses[len(snapshots)-1]
            if isinstance(value, Exception):
                raise value
            return value
        with tempfile.TemporaryDirectory() as directory, \
                patch('core.candidate_audit.independent_preview', return_value={'status': reference}), \
                patch('core.candidate_audit._render', return_value=Image.new('RGB', (10, 10))), \
                patch('core.candidate_audit.inspect_page', side_effect=inspect):
            source = Path(directory)/'source.fs'; source.write_bytes(b'fixture')
            staged, report = audit_components(source, objects, candidates, first, directory,
                                               dict(audit_page_size=12, **(config or {})))
            saved = json.loads((Path(directory)/'candidate_audit/report.json').read_text(encoding='utf8'))
            self.assertEqual(saved, report)
        return objects, candidates, first, staged, report, snapshots

    def test_fix_commits_only_after_a_complete_new_visual_check(self):
        objects, candidates, first, staged, report, snapshots = self.gate([repair(), passed()])
        self.assertIn('glass', snapshots[0]['彩条'])
        self.assertNotIn('wide', snapshots[0]['彩条'])
        self.assertEqual(set(snapshots[1]['彩条']), {'blue', 'wide'})
        self.assertEqual(set(resolve_categories(objects, candidates, staged)['彩条']), {'blue', 'wide'})
        self.assertEqual(report['accepted_repairs'], 1)
        self.assertEqual(report['semantic_status'], 'passed')
        self.assertEqual(report['status'], 'review')  # No independent reference.
        self.assertFalse(report['human_reviewed'])
        self.assertNotEqual(staged, first)

    def test_failed_recheck_rolls_back_entire_local_transaction(self):
        _, candidates, first, staged, report, _ = self.gate([repair(), VisionError('unavailable')])
        self.assertEqual(staged, first)
        self.assertEqual(report['accepted_repairs'], 0)
        self.assertEqual(report['unverified_pages'], 1)
        self.assertFalse(any(c['native_type'] == 'AuditSelection' for c in candidates))

    def test_budget_after_repair_rolls_back_instead_of_certifying_unchecked_work(self):
        _, _, first, staged, report, _ = self.gate([repair()], {'audit_max_requests': 1})
        self.assertEqual(staged, first)
        self.assertEqual(report['requests'], 1)
        self.assertEqual(report['regions'][0]['problem']['kind'], 'budget')

    def test_repair_cycles_are_stopped_and_original_selection_restored(self):
        _, _, first, staged, report, _ = self.gate([repair(), repair(add=('G002',), remove=('G004',))])
        self.assertEqual(staged, first)
        self.assertEqual(report['regions'][0]['problem']['kind'], 'cycle')

    def test_repair_zero_checks_but_does_not_apply_model_edits(self):
        _, _, first, staged, report, _ = self.gate([repair()], {'audit_repair_rounds': 0})
        self.assertEqual(staged, first)
        self.assertEqual(report['regions'][0]['problem']['kind'], 'repair_limit')

    def test_independent_reference_mismatch_is_a_review_failure(self):
        mismatch = dict(verdict='review', patches=[], source_check='mismatch', issues=[
            dict(category='原稿', kind='source_mismatch', candidate_ids=[], evidence='内嵌原稿有尾窗，解析图缺失。')])
        _, _, first, staged, report, _ = self.gate([mismatch], reference='available')
        self.assertEqual(staged, first)
        self.assertEqual(report['source_check'], 'mismatch')
        self.assertEqual(report['status'], 'review')

    def test_full_pass_requires_independent_reference_and_all_pages(self):
        _, _, _, _, report, _ = self.gate([passed('consistent')], reference='available')
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['unverified_pages'], 0)

    def test_omitted_initial_candidates_are_preserved_for_paged_inspection(self):
        objects, _, _ = fixture()
        initial, _ = build_candidates(objects, [], limit=2)
        all_proposals, coverage = build_candidates(objects, [], limit=2, include_omitted=True)
        self.assertEqual([c['member_ids'] for c in all_proposals[:2]], [c['member_ids'] for c in initial])
        self.assertTrue(all(c['initial_candidate'] for c in all_proposals[:2]))
        self.assertTrue(all(not c['initial_candidate'] for c in all_proposals[2:]))
        self.assertGreater(len(all_proposals), 2)
        self.assertEqual(coverage['provided_candidates'], 2)

    def test_unproposed_leaves_are_visible_even_when_first_pass_selected_nothing(self):
        objects, candidates, _ = fixture()
        scopes = audit_regions(objects, [], decision())
        pool = audit_pool(objects, [], scopes[0])
        self.assertEqual({i for c in pool for i in c['member_ids']}, {o['id'] for o in objects})
        self.assertEqual(scopes[0]['id'], 'unassigned')

    def test_residual_body_colour_surface_precedes_large_native_graphics(self):
        objects,candidates,first=fixture()
        scope=audit_regions(objects,candidates,first)[0]
        pool=audit_pool(objects,candidates,scope)
        ids=[c['id'] for c in pool]
        # A single painted roof must not wait behind complete native groups
        # until the finite request budget is already exhausted.
        self.assertIn('wide',scope['body_ids'])
        self.assertLess(ids.index('G004'),ids.index('G100'))
        self.assertEqual(first['categories']['车漆'],['G001'])

    def test_rendered_evidence_uses_all_six_export_categories_and_preserves_members(self):
        objects, candidates, first = fixture()
        snapshot = deepcopy((objects, candidates, first))
        scope = audit_regions(objects, candidates, first)[0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            render_evidence(objects, candidates, first, scope, candidates, path)
            with Image.open(path/'selections.png') as image:
                self.assertEqual(image.size, (1600, 1020))
            for filename in ('source.png', 'unselected.png'):
                with Image.open(path/filename) as image:
                    image.verify()
        self.assertEqual((objects, candidates, first), snapshot)

    def test_request_attaches_actual_output_and_independent_evidence_in_declared_order(self):
        payload = passed('consistent')
        response = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(payload)}}]}).encode()
        with patch('vision._image_part', side_effect=lambda p: {'type': 'image_url', 'path': str(p)}), \
                patch('vision._request_bytes', return_value=response) as send:
            result = recognize(dict(objects=[dict(id='G001', bbox=[0, 0, 1, 1])], component_audit=True,
                                    audit_reference_available=True,
                                    audit_images=['selections', 'unselected', 'embedded', 'parsed']),
                               'source', 'candidates', dict(enabled=True, base_url='https://example.invalid/v1',
                                                            model='vision', api_key='test'))
        self.assertEqual(result, payload)
        body = json.loads(send.call_args.args[0].data)['messages'][1]['content']
        self.assertEqual([v['path'] for v in body[1:]], ['source', 'candidates', 'selections', 'unselected', 'embedded', 'parsed'])

    def test_nested_paint_recheck_uses_audit_root_preview_and_focus_instructions(self):
        from core.candidate_audit import inspect_page
        objects,candidates,first=fixture()
        scope=audit_regions(objects,candidates,first)[0]
        with tempfile.TemporaryDirectory() as directory, \
                patch('core.candidate_audit.render_evidence'), \
                patch('components.candidate_atlas'), \
                patch('vision.recognize',return_value=passed('consistent')) as model:
            root=Path(directory)
            inspect_page(objects,candidates,first,scope,candidates,
                         root/'region_000'/'round_00_page_000'/'body_paint_recheck',
                         {'_body_paint_check':True,'_audit_root':str(root)}, {'status':'available'})
            scene=model.call_args.args[0]
            self.assertTrue(scene['body_paint_check'])
            self.assertEqual(scene['audit_images'][-2:],
                             [str(root/'embedded_preview.png'),str(root/'parsed_page.png')])
        response=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(passed())}}]}).encode()
        with patch('vision._image_part',return_value={'type':'image_url'}), \
                patch('vision._request_bytes',return_value=response) as send:
            recognize(dict(objects=[dict(id='P1',bbox=[0,0,1,1])],component_audit=True,body_paint_check=True),
                      'source','candidates',dict(enabled=True,base_url='https://example.invalid/v1',
                                                 model='vision',api_key='test'))
        prompt=json.loads(send.call_args.args[0].data)['messages'][1]['content'][0]['text']
        self.assertIn('本次仅复验刚完成的车身转车漆迁移',prompt)
        self.assertIn('不能只将车身中的颜色隐藏或删除而漏补车漆',prompt)

    def test_missing_reference_cannot_be_falsely_certified_by_model(self):
        response = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(passed('consistent'))}}]}).encode()
        with patch('vision._image_part', return_value={'type': 'image_url'}), \
                patch('vision._request_bytes', return_value=response), self.assertRaises(VisionError):
            recognize(dict(objects=[dict(id='G001', bbox=[0, 0, 1, 1])], component_audit=True),
                      'source', 'candidates', dict(enabled=True, base_url='https://example.invalid/v1', model='vision', api_key='test'))

    def test_config_defaults_and_invalid_limits(self):
        for config in ({'audit_page_size': True}, {'audit_repair_rounds': 8}, {'audit_enabled': 'yes'},
                       {'audit_max_requests': 0}, {'audit_timeout_seconds': float('nan')}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                audit_options(config)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'settings.json'
            path.write_text(json.dumps({'audit_page_size': 1}), encoding='utf8')
            with self.assertRaises(VisionError):
                read_config(path)

    def test_stream_keepalives_cannot_extend_the_audit_deadline(self):
        from io import BytesIO
        response = BytesIO(b'data: keepalive\n')
        with patch('vision.time.monotonic', side_effect=[0, 3]), self.assertRaises(TimeoutError):
            _read_with_deadline(response, 2, 1024)
        with patch('vision.time.monotonic', return_value=0):
            self.assertEqual(_read_with_deadline(BytesIO(b'body'), 2, 1024), b'body')
            with self.assertRaises(VisionError):
                _read_with_deadline(BytesIO(b'body too large'), 2, 3)

    def test_repair_rechecks_all_pages_before_commit(self):
        objects, candidates, first = fixture()
        scope = audit_regions(objects, candidates, first)[0]
        # Page one can modify stripe membership. Page two must be inspected
        # again even if it passed after that first staged change.
        pages = [candidates[1], candidates[3], candidates[0], candidates[2],
                 candidates[4], candidates[5], candidates[6], candidates[7]]
        replies = [repair(), passed(), passed(), passed()]
        with tempfile.TemporaryDirectory() as directory, \
                patch('core.candidate_audit.audit_pool', return_value=pages), \
                patch('core.candidate_audit.independent_preview', return_value={'status':'unavailable'}), \
                patch('core.candidate_audit.inspect_page', side_effect=replies) as model:
            updated, report = audit_components('source.fs', objects, candidates, first, directory,
                                               {'audit_page_size':4})
        self.assertEqual(model.call_count, 4)
        self.assertEqual(report['verified_pages'], 2)
        self.assertEqual(report['accepted_repairs'], 1)
        self.assertEqual(set(resolve_categories(objects, candidates, updated)['彩条']), {'blue', 'wide'})

    def test_unknown_or_unsupported_source_styles_keep_global_review_status(self):
        objects, candidates, first = fixture()
        objects[4]['fill'] = 'unresolved'
        with tempfile.TemporaryDirectory() as directory, \
                patch('core.candidate_audit.independent_preview', return_value={'status':'available'}), \
                patch('core.candidate_audit._render', return_value=Image.new('RGB',(10,10))), \
                patch('core.candidate_audit.inspect_page', return_value=passed('consistent')):
            _, report = audit_components('source.fs', objects, candidates, first, directory, {})
        self.assertEqual(report['status'], 'review')
        self.assertIn('unsupported_source', [i['kind'] for i in report['unresolved']])

    def test_disabled_gate_has_no_rendering_or_network_side_effects(self):
        objects, candidates, first = fixture()
        with patch('core.candidate_audit.inspect_page') as model, \
                patch('core.candidate_audit.independent_preview') as reference:
            updated, report = audit_components('source.fs',objects,candidates,first,'unused',{'audit_enabled':False})
        self.assertEqual(updated, first)
        self.assertEqual(report['status'], 'not_run')
        model.assert_not_called(); reference.assert_not_called()

    def test_missing_motif_on_a_later_page_is_searched_then_rechecked(self):
        objects, candidates, first = fixture()
        queued = [candidates[0], candidates[2], candidates[4], candidates[5],
                  candidates[1], candidates[3], candidates[6], candidates[7]]
        missing = dict(verdict='review', source_check='not_assessed', patches=[], issues=[
            dict(category='彩条',kind='missing',candidate_ids=[],evidence='曲线不完整，当前页没有对应对象。')])
        with tempfile.TemporaryDirectory() as directory, \
                patch('core.candidate_audit.audit_pool', return_value=queued), \
                patch('core.candidate_audit.independent_preview', return_value={'status':'unavailable'}), \
                patch('core.candidate_audit.inspect_page', side_effect=[missing,repair(),passed(),passed()]) as model:
            updated, report = audit_components('source.fs',objects,candidates,first,directory,{'audit_page_size':4})
        self.assertEqual(model.call_count, 4)
        self.assertEqual(report['semantic_status'], 'passed')
        self.assertEqual(report['accepted_repairs'], 1)
        self.assertEqual(set(resolve_categories(objects,candidates,updated)['彩条']), {'blue','wide'})

    def test_wireframe_fill_none_is_supported_for_source_inspection(self):
        objects,candidates,first=fixture()
        objects[1].update(fill='none',fill_status='wireframe_only',stroke='#000000')
        with tempfile.TemporaryDirectory() as directory, \
                patch('core.candidate_audit.independent_preview',return_value={'status':'available'}), \
                patch('core.candidate_audit._render',return_value=Image.new('RGB',(10,10))), \
                patch('core.candidate_audit.inspect_page',return_value=passed('consistent')):
            _,report=audit_components('source.fs',objects,candidates,first,directory,{})
        self.assertEqual(report['status'],'passed')

    def test_rendering_time_is_charged_before_starting_another_request(self):
        from core.candidate_audit import inspect_page
        objects,candidates,first=fixture()
        scope=audit_regions(objects,candidates,first)[0]
        with tempfile.TemporaryDirectory() as directory, \
                patch('core.candidate_audit.render_evidence'), \
                patch('components.candidate_atlas'), \
                patch('core.candidate_audit.time.monotonic',return_value=11), \
                patch('vision.recognize') as model, self.assertRaises(VisionError):
            inspect_page(objects,candidates,first,scope,candidates,Path(directory),
                         {'_audit_deadline':10},{'status':'unavailable'})
        model.assert_not_called()


if __name__ == '__main__':
    unittest.main()
