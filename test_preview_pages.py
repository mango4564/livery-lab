"""Page discovery, page isolation, and all-page vector preview regressions."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from core.fs_scene import source_preview_pages
from vector_preview import VectorDocument, VectorImage


class PageDiscoveryTests(unittest.TestCase):
    def test_numeric_pages_exclude_master_and_retain_blank_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'sample.fs'
            source.write_bytes(b'fixture')
            result = {'source':str(source), 'source_sha256':hashlib.sha256(b'fixture').hexdigest()}
            ole = MagicMock()
            ole.__enter__.return_value.listdir.return_value = [
                ['FlexiSIGN Contents','Master Page','Objects'],
                ['FlexiSIGN Contents','Page 10','Objects'],
                ['FlexiSIGN Contents','Page 2','Objects'],
                ['FlexiSIGN Contents','Page 1','Objects'],
                ['Other','Page 3','Objects']]
            def read(result, page, verify):
                if page == 'Page 2':
                    raise ValueError('unsupported page data')
                return [{'id':'page-one'}] if page == 'Page 1' else []
            with patch('core.fs_scene.olefile.OleFileIO',return_value=ole), \
                    patch('core.fs_scene.source_preview_objects',side_effect=read) as loader:
                pages = source_preview_pages(result)
            self.assertEqual([page['name'] for page in pages], ['Page 1','Page 2','Page 10'])
            self.assertEqual(loader.call_count, 3)
            self.assertEqual(pages[1]['error'], 'unsupported page data')
            self.assertEqual(pages[2]['objects'], [])
            self.assertIsNone(pages[2]['error'])

    def test_changed_source_is_rejected_before_page_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'sample.fs'
            source.write_bytes(b'changed')
            with self.assertRaises(ValueError), patch('core.fs_scene.olefile.OleFileIO') as loader:
                source_preview_pages({'source':str(source),'source_sha256':'old'})
            loader.assert_not_called()


class DocumentRenderingTests(unittest.TestCase):
    def document(self):
        def image(color):
            return VectorImage('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60">'
                               f'<path d="M0 0H100V60H0Z" fill="{color}"/></svg>')
        return VectorDocument([{'label':'Page 1','image':image('red')},
                               {'label':'Page 2','image':image('blue')}])

    def test_all_pages_are_visible_without_overlapping(self):
        doc = self.document()
        tile, _, _ = doc.render(1,(0,0),(int(doc.width),int(doc.height)))
        self.assertEqual(tile.getpixel((50,100)), (255,0,0,255))
        self.assertEqual(tile.getpixel((50,250)), (0,0,255,255))

    def test_zoom_can_reach_second_page_with_bounded_bitmap(self):
        doc = self.document()
        x,y = doc.positions[1]
        tile, _, _ = doc.render(32,(-x*32,-y*32),(200,150))
        self.assertEqual(tile.size,(200,150))
        self.assertEqual(tile.getpixel((100,100)),(0,0,255,255))


if __name__ == '__main__':
    unittest.main()
