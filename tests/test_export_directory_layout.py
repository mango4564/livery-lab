"""Category folders, recursive checksums and legacy publication history."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from reviewed_exports import LABELS, export_reviewed
from test_reviewed_exports import fixture


class ExportDirectoryLayoutTests(unittest.TestCase):
    def test_six_categories_and_base_have_resolvable_records_and_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = fixture(root)
            choices = dict(result['component_categories'], 车漆=['5'])
            patterns = {name: [{'source_view': '左视图', 'selected_ids': ids}]
                        for name, ids in choices.items()}
            published = export_reviewed(result, choices, root/'out', patterns=patterns)
            folder = Path(published['output_dir'])
            self.assertEqual({p.name for p in folder.iterdir()},
                             {'车身', '车漆', '彩条', '文字', 'logo', '门徽', '整车底图', '复核记录.json'})
            record = json.loads((folder/'复核记录.json').read_text(encoding='utf8'))
            self.assertEqual(record, published['manifest'])
            for category, label in LABELS.items():
                entry = record['categories'][label]
                self.assertEqual(entry['files'], entry['patterns'][0]['files'])
                self.assertEqual(len(entry['files']), 2)
                for name in entry['files']:
                    self.assertEqual(Path(name).parent.as_posix(), category)
                    self.assertTrue((folder/name).is_file())
            self.assertEqual({p.name for p in (folder/'整车底图').iterdir()},
                             {'vehicle-base.svg', 'vehicle-base.png', 'vehicle-base.placements.json'})
            artifacts = {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in folder.rglob('*') if p.is_file() and p.name != '复核记录.json'}
            self.assertEqual(record['artifact_sha256'], artifacts)
            self.assertEqual(len(artifacts), 15)

    def test_reexport_of_flat_legacy_folder_keeps_original_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = fixture(root)
            folder = root/'out'/Path(result['source']).stem
            folder.mkdir(parents=True)
            legacy = {'source': result['source'], 'categories': {}}
            (folder/'复核记录.json').write_text(json.dumps(legacy), encoding='utf8')
            old_svg = folder/'彩条_旧成品.svg'
            old_svg.write_text('<svg/>', encoding='utf8')
            original = {p.name: p.read_bytes() for p in folder.iterdir()}
            published = export_reviewed(result, result['component_categories'], root/'out')
            history = Path(published['previous_version'])
            self.assertEqual({p.name: p.read_bytes() for p in history.iterdir()}, original)
            self.assertFalse(old_svg.exists())
            self.assertTrue(list((folder/'彩条').glob('*.svg')))
            self.assertFalse((folder/'车漆').exists())
            self.assertFalse((folder/'整车底图').exists())


if __name__ == '__main__':
    unittest.main()
