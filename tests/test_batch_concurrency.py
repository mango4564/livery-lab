"""Bounded scheduling and exact image reuse; all model responses are offline."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image
import components
import vision
from test_components import decision
from tests.test_body_surface_audit import scene


class BatchConcurrencyTests(unittest.TestCase):
    def test_bounded_overlap_preserves_order_and_caller_thread_preparation(self):
        caller = threading.get_ident()
        barrier = threading.Barrier(2)
        finished = []
        def prepare(number, batch):
            self.assertEqual(threading.get_ident(), caller)
            return number
        def classify(batch, number):
            barrier.wait(timeout=3)
            finished.append(number)
            return number * 10
        result = list(components._ordered_batches([[i] for i in range(6)],
                      {'component_concurrency': 2}, prepare, classify))
        self.assertEqual([r[2] for r in result], [10, 20, 30, 40, 50, 60])
        self.assertEqual(sorted(finished), list(range(1, 7)))

    def test_failure_propagates_without_submitting_remaining_batches(self):
        prepared = []
        def prepare(number, batch):
            prepared.append(number)
            return number
        def classify(batch, number):
            raise vision.VisionError('offline')
        with self.assertRaisesRegex(vision.VisionError, 'offline'):
            list(components._ordered_batches([[i] for i in range(10)],
                 {'component_concurrency': 2}, prepare, classify))
        self.assertEqual(prepared, [1, 2])

    def test_serial_and_parallel_classification_merge_identically(self):
        candidates = [dict(id=f'G{i:03}', member_ids=[f'p{i}']) for i in range(8)]
        def classify(batch, *args, **kwargs):
            return decision(车身=[batch[0]['id']], 彩条=[batch[-1]['id']])
        original = decision()
        before = deepcopy(original)
        with tempfile.TemporaryDirectory() as tmp, \
                patch('components.candidate_atlas'), \
                patch('components.detail_candidates', return_value=[]), \
                patch('components.recognize_groups', side_effect=classify):
            root = Path(tmp)
            (root/'source_scene.png').write_bytes(b'unchanged')
            results = [components.recover_empty_classification(candidates, original, root,
                       {'component_concurrency': n}, batch_size=2) for n in (1, 2, 3)]
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])
        self.assertEqual(original, before)

    def test_audit_and_export_source_memberships_are_identical(self):
        results = []
        for concurrency in (1, 2, 3):
            objects, candidates, first = scene()
            def classify(batch, *args, **kwargs):
                return decision(车漆=[c['id'] for c in batch if c['member_ids']==['roof']],
                                彩条=[c['id'] for c in batch if c['member_ids']==['stripe']])
            with tempfile.TemporaryDirectory() as tmp, \
                    patch('components.candidate_atlas'), patch('components.rasterize'), \
                    patch('components.batch_inputs'), \
                    patch('components.recognize_groups', side_effect=classify):
                def prepare(batch, local, *args):
                    local.mkdir(parents=True, exist_ok=True)
                components.batch_inputs.side_effect = prepare
                merged, coverage = components.refine_body_surfaces(objects, candidates, first,
                    Path(tmp), {'component_batch_size': 2, 'component_concurrency': concurrency})
                results.append((merged, coverage, components.resolve_categories(objects, candidates, merged)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])

    def test_concurrency_config_is_bounded_and_defaults_to_serial(self):
        self.assertEqual(vision.tuning_options({})['component_concurrency'], 1)
        for value in (0, 4, True, 1.5, 'bad', None):
            with self.subTest(value=value), self.assertRaises(vision.VisionError):
                vision.tuning_options({'component_concurrency': value})


class ExactImageCacheTests(unittest.TestCase):
    def test_exact_bytes_thread_safety_invalidation_and_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'image.png'
            Image.new('RGB', (30, 20), 'blue').save(path)
            expected = vision._image_part(path)
            cache = vision.ImageInputCache(2048)
            with patch('vision._image_part', wraps=vision._image_part) as read:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    parts = list(pool.map(cache.part, [path]*12))
                self.assertEqual(read.call_count, 1)
            self.assertTrue(all(part == expected for part in parts))
            parts[0]['image_url']['detail'] = 'low'
            self.assertEqual(cache.part(path), expected)
            Image.new('RGB', (31, 20), 'red').save(path)
            self.assertEqual(cache.part(path), vision._image_part(path))
            self.assertNotEqual(cache.part(path), expected)
            self.assertLessEqual(cache.bytes, cache.max_bytes)
            small = vision.ImageInputCache(1)
            self.assertEqual(small.part(path), vision._image_part(path))
            self.assertEqual(small.bytes, 0)

    def test_request_payload_unchanged_and_parallel_trace_summary_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root/'image.png'
            Image.new('RGB', (30, 20), 'blue').save(image)
            config = dict(enabled=True, base_url='https://offline.invalid/v1', model='qwen-test', api_key='test')
            scene = dict(objects=[dict(id='G001', bbox=[0,0,10,2])], component_mode=True,
                         request_trace_dir=str(root/'ai_requests'), group_metadata=[])
            reply = json.dumps({'choices':[{'finish_reason':'stop', 'message':{'content':json.dumps(
                dict(labels={'G001':'彩条'}, reason='complete', confidence=.9))}}]}).encode()
            bodies = []
            def open_request(req, **kwargs):
                bodies.append(json.loads(req.data))
                response = MagicMock()
                response.__enter__.return_value = response
                response.read.return_value = reply
                return response
            opener = MagicMock()
            opener.open.side_effect = open_request
            with patch('vision._network_opener', return_value=opener):
                vision.recognize(scene, image, image, config)
                cached = dict(config, component_concurrency=2, _image_cache=vision.ImageInputCache())
                with ThreadPoolExecutor(max_workers=3) as pool:
                    list(pool.map(lambda _: vision.recognize(scene, image, image, cached), range(12)))
            self.assertTrue(all(body == bodies[0] for body in bodies))
            summary = json.loads((root/'ai_requests.json').read_text(encoding='utf8'))
            self.assertEqual(summary['logical_requests'], 13)
            self.assertEqual(summary['failed_requests'], 0)
            self.assertEqual(len(list((root/'ai_requests').glob('*.json'))), 13)


if __name__ == '__main__':
    unittest.main()
