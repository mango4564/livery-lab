"""Component orchestration performs classification only; offline."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import components
import engine
from tests.test_component_protocol_repair import decision
from tests.test_internal_model_review import scene
from vision import read_config


class SinglePassComponentsTests(unittest.TestCase):
    def test_conflict_candidates_are_visible_in_their_normal_gallery_categories(self):
        from review_gallery import ReviewGallery
        from tests.test_review_candidate_dedup import gallery_state
        objects, candidates, first = scene()
        first['classification_unresolved'] = [dict(kind='source_conflict',
            candidates=[['彩条', 'G002'], ['车漆', 'G004']])]
        records = components.review_candidates(candidates, first)
        gallery = gallery_state(objects, records)
        for category, gid in [('彩条', 'G002'), ('车漆', 'G004')]:
            ReviewGallery._switch_category(gallery, category)
            self.assertIn(gid, [c['id'] for c in gallery.filtered])

    def test_export_keeps_first_pass_without_additional_model_calls(self):
        objects, candidates, first = scene()
        for c in candidates:
            c['source_span'] = [0, 100]
        first['categories']['彩条'] = ['G004']
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'fixture.fs'
            source.write_bytes(b'offline')
            with patch('components.prepare', return_value=(objects, candidates, {})), \
                    patch('vision.recognize', return_value=first) as model, patch('components.rasterize'):
                _, report = components.run(source, directory, {'max_candidates': 120, 'timeout_seconds': 90})
            self.assertEqual(model.call_count, 1)
            self.assertEqual(report['decision']['categories'], first['categories'])
            self.assertEqual(report['categories']['彩条'], ['caption'])
            self.assertNotIn('quality_gate', report)
            self.assertEqual(set(report['coverage']['stage_seconds']), {'prepare', 'classification'})
            self.assertFalse((Path(directory)/'candidate_audit').exists())
            self.assertFalse((Path(directory)/'body_surface_audit').exists())
            self.assertTrue((Path(directory)/'彩条.svg').exists())
            self.assertEqual(source.read_bytes(), b'offline')

    def test_manual_mode_never_calls_model(self):
        objects, candidates, _ = scene()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'fixture.fs'
            source.write_bytes(b'offline')
            with patch('components.prepare', return_value=(objects, candidates, {})), \
                    patch('vision.recognize') as model, patch('components.rasterize'):
                _, report = components.run(source, directory, {'max_candidates': 120}, use_ai=False)
        model.assert_not_called()
        self.assertEqual(report['decision']['categories'], decision()['categories'])

    def test_retired_config_does_not_reenable_stages_or_change_cache(self):
        config = {'enabled': True, 'timeout_seconds': 90}
        with patch.object(engine, 'read_config', return_value=config):
            before = engine._fingerprint({'use_ai': True, 'extraction_mode': 'components'})
            config.update(audit_enabled=True, audit_max_requests=64, fast_mode=False)
            self.assertEqual(before, engine._fingerprint({'use_ai': True, 'extraction_mode': 'components'}))
            config['timeout_seconds'] = 240
            self.assertNotEqual(before, engine._fingerprint({'use_ai': True, 'extraction_mode': 'components'}))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'settings.json'
            path.write_text(json.dumps({'audit_enabled': 'retired', 'audit_page_size': -1}), encoding='utf8')
            self.assertEqual(read_config(path)['timeout_seconds'], 90)


if __name__ == '__main__':
    unittest.main()
