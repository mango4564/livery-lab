import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import benchmark_ai_extraction as bench


def candidate(gid, members, bounds=(0, 0, 100, 50)):
    return {'id': gid, 'member_ids': members, 'bbox': list(bounds), 'members': [],
            'native_type': 'AGroup', 'area': 5000, 'source_span': [0, 1]}


class BenchmarkSafetyTests(unittest.TestCase):
    def test_shared_source_overrides_nearest_spatial_anchor(self):
        left = candidate('A', ['x'], (0, 0, 10, 10))
        right = candidate('B', ['y'], (100, 0, 110, 10))
        item = candidate('C', ['x'], (100, 0, 110, 10))
        self.assertEqual(bench.owner(item, [left, right]), 'A')
        with self.assertRaises(ValueError):
            bench.owner(item, [])

    def test_nested_anchor_removed_without_removing_other_vehicle(self):
        candidates = [candidate('A', ['x', 'y']), candidate('B', ['x']), candidate('C', ['z'])]
        result = bench.maximal_anchors(candidates, {'categories': {'车身': ['A', 'B', 'C']}})
        self.assertEqual([x['id'] for x in result], ['A', 'C'])

    def test_strict_labels_reject_partial_or_invalid_response(self):
        valid = {'labels': {'P1': '彩条'}, 'reason': '', 'confidence': 0.5}
        self.assertEqual(bench.validate_labels(valid, ['P1'], ['彩条']), valid)
        for changed in [{'labels': {}}, {'labels': {'P1': '其他'}},
                        {'confidence': math.nan}, {'confidence': True}, {'extra': 1}]:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                bench.validate_labels(valid | changed, ['P1'], ['彩条'])

    def test_agreement_is_symmetric_difference_not_accuracy(self):
        result = bench.agreement(['a', 'b'], ['b', 'c'])
        self.assertEqual(result, {'intersection': 1, 'only_this': 1, 'only_other': 1, 'jaccard': 1/3})

    def test_equivalent_overlaid_source_copies_are_not_false_omissions(self):
        reference = {'status': 'review_fixture', 'acceptable_source_ids': ['a', 'b', 'c'],
                     'patterns': [{'alternatives': [['a', 'b'], ['c']]}]}
        score = bench.visual_reference_score(['c', 'unexpected'], reference)
        self.assertEqual(score['complete_units'], 1)
        self.assertEqual(score['missing_paths_minimum_across_alternatives'], 0)
        self.assertEqual(score['extra_source_paths'], 1)

    def test_cli_does_not_call_provider_without_explicit_execute(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)/'input.fs'
            source.write_bytes(b'fixture')
            output = Path(temp)/'run'
            profile = {'model': 'test', 'base_url': 'http://example.invalid/v1'}
            with patch.object(bench, 'load_profile', return_value=profile), \
                    patch.object(bench.comp, 'prepare'), \
                    patch.object(bench, 'render_comparisons'), \
                    patch.object(bench, 'run_provider') as provider:
                self.assertEqual(bench.main(['--source', str(source), '--output', str(output)]), 0)
                self.assertEqual(bench.main(['--plan', str(output/'plan.json'), '--summary-only']), 0)
                provider.assert_not_called()

    def test_cached_baseline_reuses_exact_input_and_rejects_changed_image(self):
        item = candidate('G001', ['shape'])
        answer = bench.decision_from_labels({'G001': '彩条'})
        profile = {'model': 'test', 'base_url': 'http://private.invalid/v1',
                   'api_key': 'secret-must-never-be-written'}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'source.fs'
            source.write_bytes(b'source')
            images = root/'run'
            images.mkdir()
            atlas = images/'group_atlas.png'
            atlas.write_bytes(b'image-before')

            def run(*args, **kwargs):
                return bench.comp.recognize_groups([item], images, profile, classification_batch=True)

            with patch.object(bench.comp, 'recognize_groups', return_value=answer) as classify, \
                    patch.object(bench.comp, 'run', side_effect=run):
                self.assertEqual(bench.cached_baseline(source, images, profile), answer)
                self.assertEqual(bench.cached_baseline(source, images, profile), answer)
                atlas.write_bytes(b'image-changed')
                with self.assertRaises(ValueError):
                    bench.cached_baseline(source, images, profile)
                self.assertEqual(classify.call_count, 1)
            cache = (images/'benchmark_cache.json').read_text(encoding='utf8')
            self.assertNotIn(profile['api_key'], cache)
            self.assertNotIn(profile['base_url'], cache)

    def test_bootstrap_cost_survives_baseline_failure_and_network_retries(self):
        request_record = {'id': 'startup', 'stage': 'classification', 'status': 'accepted',
                          'elapsed_seconds': 12, 'attempts': [{}, {}]}
        startup = {'logical_requests': 1, 'http_attempts': 2, 'retries': 1,
                   'failed_requests': 0, 'elapsed_seconds': 12,
                   'stages': {'classification': 1}, 'requests': [request_record]}
        local = startup | {'http_attempts': 1, 'retries': 0, 'elapsed_seconds': 5,
                           'stages': {'guarded_local': 1}, 'requests': []}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'source.fs'
            source.write_bytes(b'unchanged')
            plan = {'output': str(root), 'cases': [{'id': 'case_01', 'source': str(source),
                                                   'source_sha256': bench.file_hash(source)}]}
            bench.save(root/'case_01/qwen_baseline/failure.json',
                       {'error_type': 'ValueError', 'ai_request_summary': startup})
            bench.save(root/'case_01/qwen_structural/benchmark_report.json', {
                'status': 'experimental_review', 'stripe_source_ids': [],
                'bootstrap_request_ids': ['startup'], 'ai_request_summary': local})
            with patch.object(bench, 'diagnose_initial', return_value=None):
                result = bench.summarize(plan)['cases'][0]['methods']
            self.assertEqual(result['qwen_baseline']['status'], 'failed')
            self.assertEqual(result['qwen_structural']['bootstrap_requests'], 1)
            self.assertEqual(result['qwen_structural']['bootstrap_http_attempts'], 2)
            self.assertEqual(result['qwen_structural']['including_bootstrap_seconds'], 17)

    def test_local_review_protects_text_and_reopens_paint_without_gemini(self):
        objects = [{'id': i, 'bbox': [0, 0, 10, 10], 'source_offset': n}
                   for n, i in enumerate(['decoration', 'paint', 'text', 'structure'])]
        candidates = [candidate('G001', [o['id'] for o in objects]),
                      candidate('G002', ['decoration', 'text']),
                      candidate('G003', ['paint']), candidate('G004', ['text'])]
        for c in candidates:
            c['members'] = [o for o in objects if o['id'] in c['member_ids']]
        calls = []

        def classify(batch, directory, anchor, **kwargs):
            calls.append((kwargs['stage'], batch))
            if kwargs['stage'] == 'associated_initial':
                labels = {'G001': '车身', 'G002': '混合装饰组', 'G003': '车漆', 'G004': '文字'}
                return {'labels': {c['id']: labels[c['id']] for c in batch}}
            return {'labels': {c['id']: '彩条' for c in batch}}

        with tempfile.TemporaryDirectory() as temp, \
                patch.object(bench.comp, 'prepare', return_value=(objects, candidates, {})), \
                patch.object(bench.comp, 'residual_surface_candidates', return_value=([], 0)), \
                patch.object(bench, 'structural_call', side_effect=classify), \
                patch.object(bench, 'export_stripes'), \
                patch.object(bench.vision, 'request_summary', return_value={'requests': []}):
            source = Path(temp)/'test.fs'
            source.write_bytes(b'unchanged')
            report = bench.run_structural(source, Path(temp)/'run', {}, {
                'decision': {'categories': {'车身': ['G001']}}, 'ai_request_summary': {'requests': []}})
        local = [c for stage, batch in calls if stage == 'guarded_local' for c in batch]
        self.assertEqual({i for c in local for i in c['member_ids']}, {'paint', 'decoration'})
        self.assertEqual(set(report['stripe_source_ids']), {'paint', 'decoration'})
        self.assertEqual(report['protected_source_overlap'], 0)


if __name__ == '__main__':
    unittest.main()
