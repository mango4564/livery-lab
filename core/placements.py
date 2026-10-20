"""Versioned, non-rendering placement hints for human-reviewed vehicle bases."""
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET

from PIL import Image, PngImagePlugin

from core.paint import CATEGORY_KEYS
from geometry import transform_bounds

SVG = 'http://www.w3.org/2000/svg'
PNG_KEY = 'livery-lab.placements'
GRAPHICS = ('彩条', 'logo', '文字', '门徽')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(',', ':'))


def _box(bounds):
    """All public boxes use x/y/width/height, in the named coordinate system."""
    x0, y0, x1, y1 = bounds
    return [x0, y0, x1-x0, y1-y0]


def _source_bounds(objects):
    boxes = [o.get('bbox') for o in objects]
    if any(not isinstance(b, (list, tuple)) or len(b) != 4
           or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in b)
           or b[2] < b[0] or b[3] < b[1] for b in boxes):
        raise ValueError('历史图案缺少有效源边界，无法生成位置槽位。')
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def attach_placements(svg, png_path, objects, patterns, regions, source_sha256,
                      *, individual_patterns):
    """Return SVG and shared payload; embed that payload in the existing PNG.

    PNG mapping follows rasterize's Skia viewport and scale, including meet
    letterboxing, height cap and the final ceil-sized transparent pixel edge.
    No geometry, pixels or source-order rendering is changed.
    """
    from vector_preview import VectorImage

    root = ET.fromstring(svg)
    viewbox = list(map(float, root.get('viewBox').split()))
    scene = root.find(f'{{{SVG}}}g[@id="source-coordinate-system"]')
    match = re.fullmatch(r'translate\(0 ([^)]+)\) scale\(1 -1\)', scene.get('transform', ''))
    if not match:
        raise ValueError('底图源坐标变换不受支持。')
    source_to_svg = [1, 0, 0, -1, 0, float(match[1])]
    vector = VectorImage(svg)
    width, height = vector.width, vector.height
    scale = min(2400 / max(width, 1), 4000 / max(height, 1))
    unit = min(width/viewbox[2], height/viewbox[3])
    svg_to_png = [unit*scale, 0, 0, unit*scale,
                  ((width-viewbox[2]*unit)/2-viewbox[0]*unit)*scale,
                  ((height-viewbox[3]*unit)/2-viewbox[1]*unit)*scale]
    with Image.open(png_path) as image:
        png_size = list(image.size)
    expected = [max(1, math.ceil(width*scale)), max(1, math.ceil(height*scale))]
    if png_size != expected:
        raise ValueError('底图 PNG 尺寸与位置映射不一致。')

    by_id = {o['id']: o for o in objects}
    order = {o['id']: i for i, o in enumerate(objects)}
    slots, slot_ids = [], set()
    for name in GRAPHICS:
        for entry in patterns[name]:
            ids = sorted(entry['selected_ids'], key=order.__getitem__)
            bounds = _source_bounds([by_id[i] for i in ids])
            svg_bounds = transform_bounds(bounds, source_to_svg)
            png_bounds = transform_bounds(svg_bounds, svg_to_png)
            key = {'category': CATEGORY_KEYS[name], 'source_ids': ids,
                   'source_region': entry.get('source_region')}
            slot_id = 'slot-' + hashlib.sha256(_json(key).encode('utf8')).hexdigest()
            if slot_id in slot_ids:
                raise ValueError('历史图案扣除其他类别后位置重复，请合并重复图案后重新复核。')
            slot_ids.add(slot_id)
            region_id = entry.get('source_region')
            source_view = entry.get('source_view', '待确认视图')
            reasons = []
            if not individual_patterns:
                reasons.append('legacy_category_group')
            if not region_id:
                reasons.append('region_unassigned')
            if source_view not in ('左视图', '右视图', '前视图', '后视图', '顶视图', '底视图'):
                reasons.append('view_unresolved')
            if bounds[2] == bounds[0] or bounds[3] == bounds[1]:
                reasons.append('degenerate_bounds')
            slots.append(dict(key, id=slot_id, label=name, source_view=source_view,
                              region_name=entry.get('region_name'),
                              bbox_source=_box(bounds), bbox_svg=_box(svg_bounds),
                              bbox_png=_box(png_bounds),
                              center_svg=[(svg_bounds[0]+svg_bounds[2])/2,
                                          (svg_bounds[1]+svg_bounds[3])/2],
                              source_order_indices=[order[i] for i in ids],
                              files=entry.get('files', []),
                              placement_status='requires_confirmation' if reasons else 'suggested',
                              confirmation_reasons=reasons,
                              fit='contain', align='center', rotation_degrees=None,
                              orientation_status='not_inferred', mirror=False,
                              clip_to_historical_shape=False, related_slot_ids=[]))
    for slot in slots:
        slot['related_slot_ids'] = [other['id'] for other in slots
            if other is not slot and set(slot['source_ids']) & set(other['source_ids'])]

    region_records = []
    for region in regions:
        bounds = _source_bounds([{'bbox': region.get('bbox')}])
        region_records.append({'id': region['id'], 'name': region['name'],
                               'source_view': region['source_view'],
                               'bbox_source': _box(bounds),
                               'bbox_svg': _box(transform_bounds(bounds, source_to_svg))})
    data = {'schema': 'livery-lab.vehicle-placements', 'schema_version': 1,
            'source_sha256': source_sha256, 'human_reviewed': True,
            'base_content_sha256': hashlib.sha256(svg.encode('utf8')).hexdigest(),
            'physical_scale_verified': False,
            'canvas': {'svg_viewbox': viewbox, 'svg_size': [width, height],
                       'png_size': png_size, 'source_to_svg': source_to_svg,
                       'svg_to_png': svg_to_png, 'bbox_format': 'xywh'},
            'regions': region_records, 'slots': slots,
            'limitations': ['orientation_not_inferred', 'occlusion_not_reconstructed',
                            'no_automatic_cross_view_linking']}
    data['template_id'] = 'vehicle-' + hashlib.sha256(_json(data).encode('utf8')).hexdigest()
    metadata = root.find(f'{{{SVG}}}metadata')
    info = json.loads(metadata.text)
    info['placements'] = data
    metadata.text = _json(info)
    png_info = PngImagePlugin.PngInfo()
    png_info.add_itxt(PNG_KEY, _json(data), zip=True)
    with Image.open(png_path) as image:
        pixels = image.copy()
    pixels.save(png_path, pnginfo=png_info)
    return ET.tostring(root, encoding='unicode'), data
