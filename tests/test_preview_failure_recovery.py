"""A failed page must be read again when a fresh result arrives."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from core.fs_scene import source_preview_pages
from ui import LiveryApp


class PreviewFailureRecoveryTests(unittest.TestCase):
    def test_keyerror_retains_type_and_location_instead_of_only_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'sample.fs'
            source.write_bytes(b'fixture')
            result = {'source': str(source), 'source_sha256': hashlib.sha256(b'fixture').hexdigest()}
            ole = MagicMock()
            ole.__enter__.return_value.listdir.return_value = [['FlexiSIGN Contents', 'Page 1', 'Objects']]
            with patch('core.fs_scene.olefile.OleFileIO', return_value=ole), \
                    patch('core.fs_scene.source_preview_objects', side_effect=KeyError(0)):
                pages = source_preview_pages(result)
        self.assertEqual(pages[0]['error_type'], 'KeyError')
        self.assertIn('KeyError: 0', pages[0]['error_details'])
        self.assertIn('source_preview_objects', pages[0]['error_details'])

    def test_failed_document_is_reparsed_and_successful_document_reused(self):
        result = {'source': 'sample.fs', 'source_sha256': 'digest'}
        failed = [{'name': 'Page 1', 'objects': [], 'error': '0', 'error_type': 'KeyError',
                   'error_details': 'Traceback: source_preview_objects -> KeyError: 0'}]
        success = [{'name': 'Page 1', 'objects': [{'id': 'red', 'd': 'M0 0 H10 V10 H0 Z',
                    'fill': '#ff0000', 'bbox': [0, 0, 10, 10]}], 'error': None}]
        with patch('core.fs_scene.source_preview_pages', side_effect=[failed, success]) as read:
            initial = LiveryApp._prepare_previews(result)
            self.assertTrue(any('KeyError: 0' in m for m in initial[4]))
            recovered = LiveryApp._prepare_previews(result, source_vector=initial[1])
            self.assertEqual(read.call_count, 2)
            self.assertIsNone(recovered[1].pages[0]['error'])
            self.assertEqual(recovered[4], [])
            image = recovered[1].pages[0]['image']
            pixels, _, _ = image.render(1, (0, 0), (100, 100))
            self.assertEqual(pixels.convert('RGBA').getpixel((50, 50)), (255, 0, 0, 255))
            reused = LiveryApp._prepare_previews(result, source_vector=recovered[1])
            self.assertEqual(read.call_count, 2)
            self.assertIs(reused[1], recovered[1])


if __name__ == '__main__':
    unittest.main()
