"""Publish only explicitly reviewed category choices to a clean per-FS folder."""
import hashlib
import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from geometry import rasterize
from core.svg_layers import layered_svg

LABELS = {'车身': '车身', '彩条': '彩条', '文字': '文字', 'logo': 'logo', '门徽': '徽章', '车漆': '车漆'}


def export_reviewed(result, choices, output_root):
    if set(choices) != set(LABELS):
        raise ValueError('请逐类确认最佳图案；没有图案的类别请选择“该类没有图案”。')
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
    for name in LABELS:
        if name == '车身':
            continue
        if other & set(selected[name]):
            raise ValueError('车漆、彩条、文字、logo、徽章之间有重叠对象，请调整最佳图案后再导出。')
        other.update(selected[name])
    selected['车身'] = [i for i in selected['车身'] if i not in other]
    if choices['车身'] is not None and not selected['车身']:
        raise ValueError('车身扣除其他类别后为空，请重新选择。')
    if not any(selected.values()):
        raise ValueError('所有类别均无图案，没有可导出的文件。')
    from core.paint import style_capability
    if any(style_capability(by_id[i]) == 'unsupported' for i in selected['车漆']):
        raise ValueError('车漆包含未知或未支持的填充，无法保留原色或渐变，请重新复核。')
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
    try:
        if selected['车漆']:
            from geometry import union_bounds
            bounds = union_bounds([by_id[i] for ids in selected.values() for i in ids])
            categories = {n: ids if n in ('车身', '车漆') else [] for n, ids in selected.items()}
            svg, info = layered_svg(result['objects'], categories, bounds=bounds, width=2400,
                metadata={'source_sha256': digest, 'human_reviewed': True},
                layer_names=('车漆', '车身'))
            (staging/'vehicle-base.svg').write_text(svg, encoding='utf8')
            rasterize(svg, staging/'vehicle-base.png', max_width=2400)
            manifest['layer_status'] = info['layer_status']
            manifest['schema_version'] = 2
        for name, label in LABELS.items():
            ids = selected[name]
            files = []
            if ids:
                id_set = set(ids)
                objects = [o for o in result['objects'] if o['id'] in id_set]
                svg, _ = layered_svg(objects, {name: ids}, width=2400, layer_names=(name,),
                    metadata={'source_sha256': digest, 'category': label, 'selected_ids': ids,
                              'human_reviewed': True})
                stem = label + '_' + source.stem
                (staging/(stem+'.svg')).write_text(svg, encoding='utf8')
                rasterize(svg, staging/(stem+'.png'), max_width=2400)
                files = [stem+'.svg', stem+'.png']
            manifest['categories'][label] = {'status':'reviewed' if ids else 'confirmed_absent',
                                              'selected_ids':ids, 'files':files}
        if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise ValueError('导出期间源 FS 发生变化，未发布成品。')
        manifest['artifact_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in staging.iterdir() if p.is_file()}
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
