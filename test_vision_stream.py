"""Complete SSE responses must obey the same ID and JSON contract as JSON replies."""
import json
import unittest
from unittest.mock import patch
import vision


def event(value):
    return ('data: ' + json.dumps(value, ensure_ascii=False) + '\n\n').encode('utf8')


class VisionStreamTests(unittest.TestCase):
    def stream(self, content, finish='stop', done=True):
        midpoint = len(content)//2
        return (b': keepalive\n\n' +
                event({'choices': [{'index': 0, 'delta': {'role': 'assistant'}}]}) +
                event({'choices': [{'index': 0, 'delta': {'content': content[:midpoint]}}]}) +
                event({'choices': [{'index': 0, 'delta': {'content': content[midpoint:]}, 'finish_reason': finish}]}) +
                event({'choices': [], 'usage': {'total_tokens': 100}}) +
                (b'data: [DONE]\n\n' if done else b''))

    def test_sse_chinese_and_crlf(self):
        content = json.dumps({'selected_ids': ['a'], 'reason': '原始彩条', 'confidence': .9}, ensure_ascii=False)
        envelope = vision._response_envelope(self.stream(content).replace(b'\n', b'\r\n'))
        payload = json.loads(envelope['choices'][0]['message']['content'])
        self.assertEqual(vision._validate_selection(payload, {'a'})['selected_ids'], ['a'])

    def test_json_only_provider_still_supported(self):
        envelope = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{}'}}]}
        self.assertEqual(vision._response_envelope(json.dumps(envelope).encode()), envelope)

    def test_truncated_refused_and_partial_streams_are_rejected(self):
        for finish, done in [('length', True), ('content_filter', True), ('stop', False), (None, True)]:
            with self.subTest(finish=finish, done=done), self.assertRaises(vision.VisionError):
                vision._response_envelope(self.stream('{}', finish, done))

    def test_sse_error_and_malformed_payload_are_rejected(self):
        for value in [{'error': {'message': 'do-not-log'}}, {'choices': {}},
                      {'choices': [None]}, {'choices': [{'delta': []}]},
                      {'choices': [{'index': 1, 'delta': {}}]}]:
            with self.subTest(value=value), self.assertRaises(vision.VisionError) as caught:
                vision._response_envelope(event(value) + b'data: [DONE]\n\n')
            self.assertNotIn('do-not-log', str(caught.exception))

    def test_recognize_requests_stream_and_validates_source_ids(self):
        config = {'enabled': True, 'base_url': 'https://example.invalid/v1', 'model': 'vision',
                  'api_key': 'test-key', 'use_system_proxy': False}
        scene = {'objects': [{'id': 'a', 'bbox': [0, 0, 1, 1], 'score': 1}]}
        for oid in ['a', 'unknown']:
            content = json.dumps({'selected_ids': [oid], 'reason': '彩条', 'confidence': .9})
            with patch.object(vision, '_image_part', return_value={'type': 'image_url'}), \
                 patch.object(vision, '_request_bytes', return_value=self.stream(content)) as send:
                if oid == 'a':
                    self.assertEqual(vision.recognize(scene, 'unused', 'unused', config)['selected_ids'], ['a'])
                else:
                    with self.assertRaises(vision.VisionError):
                        vision.recognize(scene, 'unused', 'unused', config)
                req = send.call_args.args[0]
                self.assertFalse(send.call_args.kwargs['use_system_proxy'])
                self.assertTrue(json.loads(req.data)['stream'])
                self.assertEqual(req.get_header('Accept'), 'text/event-stream')


if __name__ == '__main__':
    unittest.main()
