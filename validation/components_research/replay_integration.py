"""Replay saved real-model decisions through workbench commit/cache, no network.

Run from repository root. This checks integration, not a new recognition trial.
"""
import hashlib
import json
from pathlib import Path
import shutil
import types
from unittest.mock import patch

import engine
from components import CATEGORIES, load_scene
from geometry import rasterize, svg_document, union_bounds


def main():
    checks = []
    for code in ('3261042', '3261074', '3261129'):
        recorded_dir = Path('validation/components_research') / code
        recorded = json.loads((recorded_dir/'components_report.json').read_text(encoding='utf8'))
        source = Path(recorded['source'])
        assert hashlib.sha256(source.read_bytes()).hexdigest() == recorded['source_sha256']

        def replay(source, directory, config):
            objects, _ = load_scene(source)
            valid = {o['id'] for o in objects}
            report = dict(recorded, views=[])
            for src, dst in (('source_scene.png', 'source_scene.png'), ('group_atlas.png', 'group_atlas.png')):
                shutil.copy2(recorded_dir/src, directory/dst)
            for name in CATEGORIES:
                ids = recorded['categories'][name]
                assert set(ids) <= valid
                selected = [o for o in objects if o['id'] in set(ids)]
                svg_path, png_path = directory/(name+'.svg'), directory/(name+'.png')
                svg = svg_document(selected)
                svg_path.write_text(svg, encoding='utf8')
                rasterize(svg, png_path)
                report['views'].append({'label':name+' · 记录回放', 'category':name, 'selected_ids':ids,
                                        'bbox':union_bounds(selected),'svg':str(svg_path),'png':str(png_path)})
            return objects, report

        def launch(cmd, **kwargs):
            i = cmd.index('_worker')
            src, directory, digest, fingerprint, use_ai = cmd[i+1:]
            engine._run_worker(Path(src), Path(directory), digest, fingerprint, use_ai == '1')
            return types.SimpleNamespace(returncode=0)

        with patch('components.run', side_effect=replay), patch('engine.subprocess.run', side_effect=launch) as worker:
            options = {'extraction_mode':'components','use_ai':True,'force':True}
            result = engine.process_file(source, 'validation/components_replay', options)
            options['force'] = False
            cached = engine.process_file(source, 'validation/components_replay', options)
            assert worker.call_count == 1
        assert result['component_categories'] == recorded['categories']
        assert result['output_dir'] == cached['output_dir']
        assert len(result['views']) == 5
        checks.append({'sample':code, 'kind':'recorded real-model response replay; no new inference',
                       'source_ids_valid':True, 'five_views_sealed':True, 'cache_reused':True,
                       'output_dir':result['output_dir']})
        print(code, 'recorded response -> worker -> sealed artifacts -> cache: PASS')
    Path('validation/components_research/replay_checks.json').write_text(
        json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf8')


if __name__ == '__main__':
    main()
