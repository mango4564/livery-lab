"""Publish only explicitly reviewed category choices to a clean per-FS folder."""
import hashlib
import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from geometry import rasterize
from core.svg_layers import layered_svg
from core.source_regions import object_regions, region_for_ids, split_by_region, normalize_source_regions

LABELS = {'车身': '车身', '彩条': '彩条', '文字': '文字', 'logo': 'logo', '门徽': '徽章', '车漆': '车漆'}
BASE_DIR = '整车底图'
CATEGORY_DIRS = {'车身': '车身', '彩条': '彩条', '文字': '文字', 'logo': 'logo', '门徽': '门徽', '车漆': '车漆'}
SOURCE_VIEWS = ('待确认视图', '左视图', '右视图', '前视图', '后视图', '顶视图', '底视图', '整体视图', '独立图案', '混合视图')


def infer_source_view(ids, objects, candidates):
    """Use labeled source memberships/regions, never infer direction from layout."""
    from geometry import union_bounds
    chosen = set(ids)
    exact = {c.get('source_view') for c in candidates
             if set(c.get('selected_ids', [])) == chosen
             and c.get('source_view') in SOURCE_VIEWS[1:]}
    if len(exact) == 1:
        return exact.pop()
    if exact or not chosen:
        return SOURCE_VIEWS[0]
    selected = [o for o in objects if o['id'] in chosen]
    if len(selected) != len(chosen):
        return SOURCE_VIEWS[0]
    bounds = union_bounds(selected)
    directions = set()
    for c in candidates:
        direction = c.get('source_view')
        if direction not in SOURCE_VIEWS[1:8]:
            continue
        member_ids = set(c.get('selected_ids', []))
        members = [o for o in objects if o['id'] in member_ids]
        if not members:
            continue
        a = union_bounds(members)
        if a[0] <= bounds[0] and a[1] <= bounds[1] and bounds[2] <= a[2] and bounds[3] <= a[3]:
            directions.add(direction)
    return directions.pop() if len(directions) == 1 else SOURCE_VIEWS[0]


def export_reviewed(result, choices, output_root, *, patterns=None):
    if set(choices) != set(LABELS):
        raise ValueError('请逐类确认需要导出的图案；没有图案的类别请选择“该类没有图案”。')
    source = Path(result['source']).resolve()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != result['source_sha256']:
        raise ValueError('源 FS 已变化，请重新提取后复核。')
    by_id = {obj['id']: obj for obj in result['objects']}
    if len(by_id) != len(result['objects']):
        raise ValueError('源对象 ID 不唯一。')
    selected = {}
    for name, ids in choices.items():
        if ids is None:
            selected[name] = []
            continue
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in by_id for i in ids):
            raise ValueError('类别选择包含空列表或未知对象，请重新确认。')
        if len(ids) != len(set(ids)):
            raise ValueError('类别选择包含重复对象。')
        selected[name] = list(ids)
    other = set()
    from core.paint import category_conflicts, category_exclusions
    selected['文字'] = [i for i in selected['文字'] if i not in category_exclusions('文字', selected)]
    if choices['文字'] is not None and not selected['文字']:
        raise ValueError('文字扣除 logo 后为空，请重新选择纯文字对象。')
    if category_conflicts(selected):
        raise ValueError('类别之间有重叠对象；仅 logo 与徽章可共享标志，请调整后再导出。')
    for name in LABELS:
        if name == '车身':
            continue
        other.update(selected[name])
    selected['车身'] = [i for i in selected['车身'] if i not in other]
    if choices['车身'] is not None and not selected['车身']:
        raise ValueError('车身扣除其他类别后为空，请重新选择。')
    if not any(selected.values()):
        raise ValueError('所有类别均无图案，没有可导出的文件。')
    from core.paint import style_capability
    unsupported = [i for i in selected['车漆']
                   if style_capability(by_id[i], allow_no_fill=True) == 'unsupported']
    if unsupported:
        details = '、'.join(f"{i}（{by_id[i].get('fill_status', 'unknown')}）" for i in unsupported[:8])
        raise ValueError(f'车漆包含未知或未支持的填充，无法保留原色或渐变，请重新复核。'
                         f'共 {len(unsupported)} 个对象：{details}')
    # Legacy callers retain their filenames; direction-aware reviews publish each
    # confirmed pattern separately, while category IDs remain a coverage summary.
    reviewed_patterns = {}
    regions = normalize_source_regions(result.get('source_regions', []))
    assignments = object_regions(result['objects'], regions,
                                 partial_ids=selected['彩条']+selected['logo']+selected['门徽'],
                                 paint_ids=selected['车漆'])
    if patterns is not None:
        if set(patterns) != set(LABELS):
            raise ValueError('图案记录须包含全部类别。')
        for name in LABELS:
            entries = patterns[name]
            if not isinstance(entries, list):
                raise ValueError('图案记录必须是列表。')
            seen, covered, normalized = set(), set(), []
            for entry in entries:
                if not isinstance(entry, dict) or entry.get('source_view') not in SOURCE_VIEWS:
                    raise ValueError('请为每个图案选择有效的来源视图。')
                ids = entry.get('selected_ids')
                if (not isinstance(ids, list) or not ids
                        or any(not isinstance(i, str) or i not in by_id for i in ids)
                        or len(ids) != len(set(ids))):
                    raise ValueError('图案包含空列表、重复或未知对象。')
                key = frozenset(ids)
                if key in seen:
                    raise ValueError('同一类别不能重复添加相同源对象的图案。')
                seen.add(key)
                covered.update(ids)
                excluded = category_exclusions(name, selected)
                kept = [i for i in ids if i not in excluded]
                if not kept:
                    raise ValueError('图案扣除其他类别后为空，请重新选择。')
                groups = split_by_region(kept, assignments,
                                         objects=result['objects'] if name in ('车漆', '彩条') else None)
                for group in groups:
                    pattern = {'source_view': entry['source_view'], 'selected_ids': group}
                    region = region_for_ids(group, assignments, regions)
                    if region:
                        from core.source_regions import valid_region_name
                        if not valid_region_name(region['name']):
                            raise ValueError('区域名称无效，请重新复核。')
                        pattern.update(source_region=region['id'], region_name=region['name'])
                        if pattern['source_view'] == SOURCE_VIEWS[0] or len(groups) > 1:
                            pattern['source_view'] = region['source_view']
                    elif len(groups) > 1:
                        pattern['source_view'] = SOURCE_VIEWS[0]
                    normalized.append(pattern)
            expected = set(choices[name] or [])
            if covered != expected:
                raise ValueError('图案记录与类别所选对象不一致，请重新确认。')
            reviewed_patterns[name] = normalized
        anchors = result.get('component_candidates', []) + [
            dict(entry) for entries in reviewed_patterns.values() for entry in entries]
        for entries in reviewed_patterns.values():
            for entry in entries:
                if entry['source_view'] == SOURCE_VIEWS[0] and not entry.get('source_region'):
                    entry['source_view'] = infer_source_view(entry['selected_ids'], result['objects'], anchors)
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / source.stem
    if destination.exists():
        try:
            previous = json.loads((destination/'复核记录.json').read_text(encoding='utf8'))
        except (OSError, ValueError):
            raise ValueError('目标同名目录不是本工具的复核成果，请选择其他成品保存位置。') from None
        if Path(previous.get('source', '')).resolve() != source:
            raise ValueError('目标目录已用于另一份同名 FS，请选择其他成品保存位置。')
    token = datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_' + uuid.uuid4().hex[:8]
    staging = root / ('.review_stage_' + uuid.uuid4().hex)
    backup = None
    staging.mkdir()
    manifest = {'source': str(source), 'source_sha256': digest, 'reviewed_at': datetime.now().astimezone().isoformat(),
                'status': 'human_reviewed', 'categories': {}, 'physical_scale_verified': False}
    if patterns is not None:
        manifest['schema_version'] = 3
        if regions:
            manifest['source_regions'] = regions
    try:
        if selected['车漆']:
            from geometry import union_bounds
            bounds = union_bounds([by_id[i] for ids in selected.values() for i in ids])
            categories = {n: ids if n in ('车身', '车漆') else [] for n, ids in selected.items()}
            svg, info = layered_svg(result['objects'], categories, bounds=bounds, width=2400,
                metadata={'source_sha256': digest, 'human_reviewed': True},
                layer_names=('车漆', '车身'), source_order_fallback=True)
            base_dir = staging / BASE_DIR
            base_dir.mkdir(parents=True, exist_ok=True)
            (base_dir/'vehicle-base.svg').write_text(svg, encoding='utf8')
            rasterize(svg, base_dir/'vehicle-base.png', max_width=2400)
            manifest['layer_status'] = info['layer_status']
            manifest['base_rendering'] = info['rendering']
            manifest.setdefault('schema_version', 2)
        for name, label in LABELS.items():
            ids = selected[name]
            files = []
            entries = reviewed_patterns[name] if patterns is not None else ([{'selected_ids': ids}] if ids else [])
            pattern_records = []
            folder_name = CATEGORY_DIRS[name]
            target_dir = staging / folder_name
            if entries:
                target_dir.mkdir(parents=True, exist_ok=True)
            for index, entry in enumerate(entries, 1):
                pattern_ids = entry['selected_ids']
                id_set = set(pattern_ids)
                objects = [o for o in result['objects'] if o['id'] in id_set]
                svg, _ = layered_svg(objects, {name: pattern_ids}, width=2400, layer_names=(name,),
                    metadata={'source_sha256': digest, 'category': label, 'selected_ids': pattern_ids,
                              'source_view': entry.get('source_view', '待确认视图'),
                              'source_region': entry.get('source_region'),
                              'region_name': entry.get('region_name'),
                              'human_reviewed': True})
                stem = (f"{entry.get('region_name', entry.get('source_view'))}_{label}_{index:02d}_{source.stem}"
                        if patterns is not None else label + '_' + source.stem)
                (target_dir/(stem+'.svg')).write_text(svg, encoding='utf8')
                rasterize(svg, target_dir/(stem+'.png'), max_width=2400)
                pair = [f"{folder_name}/{stem}.svg", f"{folder_name}/{stem}.png"]
                files.extend(pair)
                pattern_records.append(dict(entry, files=pair))
            manifest['categories'][label] = {'status':'reviewed' if ids else 'confirmed_absent',
                                              'selected_ids':ids, 'files':files}
            if patterns is not None:
                manifest['categories'][label]['patterns'] = pattern_records
            # Keep exact exported pattern memberships/files for placement hints.
            reviewed_patterns[name] = pattern_records
        if selected['车漆']:
            from core.placements import attach_placements
            # Old callers may only provide one category-wide selection. Split
            # by known region, but never claim it identifies individual motifs.
            placement_patterns = reviewed_patterns
            if patterns is None:
                placement_patterns = {}
                for name in LABELS:
                    entries = []
                    for group in split_by_region(selected[name], assignments,
                            objects=result['objects'] if name in ('车漆', '彩条') else None):
                        entry = {'selected_ids': group, 'files': manifest['categories'][LABELS[name]]['files']}
                        region = region_for_ids(group, assignments, regions)
                        if region:
                            entry.update(source_region=region['id'], region_name=region['name'],
                                         source_view=region['source_view'])
                        entries.append(entry)
                    placement_patterns[name] = entries
            base_dir = staging / BASE_DIR
            base_path = base_dir / 'vehicle-base.svg'
            svg, placements = attach_placements(base_path.read_text(encoding='utf8'),
                base_dir / 'vehicle-base.png', result['objects'], placement_patterns, regions,
                digest, individual_patterns=patterns is not None)
            base_path.write_text(svg, encoding='utf8')
            (base_dir/'vehicle-base.placements.json').write_text(
                json.dumps(placements, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')
            manifest['placements'] = {'schema_version': placements['schema_version'],
                                      'template_id': placements['template_id'],
                                      'file': f'{BASE_DIR}/vehicle-base.placements.json',
                                      'slot_count': len(placements['slots'])}
        if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise ValueError('导出期间源 FS 发生变化，未发布成品。')
        manifest['artifact_sha256'] = {p.relative_to(staging).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in staging.rglob('*') if p.is_file()}
        (staging/'复核记录.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf8')
        if destination.exists():
            backup = root/'.review_history'/source.stem/token
            backup.parent.mkdir(parents=True, exist_ok=True)
            destination.rename(backup)
        try:
            staging.rename(destination)
        except Exception:
            if backup is not None:
                backup.rename(destination)
            raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {'output_dir':str(destination), 'manifest':manifest,
            'previous_version':str(backup) if backup else None}
