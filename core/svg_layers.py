"""Named SVG layers, with rendered-order verification before publication."""
import json
import xml.etree.ElementTree as ET

from geometry import svg_document
from core.paint import CATEGORY_KEYS, normalize_categories

SVG = 'http://www.w3.org/2000/svg'
INK = 'http://www.inkscape.org/namespaces/inkscape'
ORDER = ('车漆', '车身', '彩条', '文字', 'logo', '门徽')


class LayerOrderError(ValueError):
    pass


def _serialize(root):
    ET.register_namespace('', SVG)
    ET.register_namespace('inkscape', INK)
    ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')
    return ET.tostring(root, encoding='unicode')


def _same_render(a, b):
    from vector_preview import VectorImage
    for width in (640, 1600):
        vectors = [VectorImage(s) for s in (a, b)]
        v = vectors[0]
        scale = min(width/v.width, 1600/v.height)
        size = (max(1, round(v.width*scale)), max(1, round(v.height*scale)))
        images = [im.render(scale, (0, 0), size)[0] for im in vectors]
        if images[0].tobytes() != images[1].tobytes():
            return False
    return True


def layered_svg(objects, categories, *, bounds=None, width=1600, strict=True,
                metadata=None, layer_names=ORDER, source_order_fallback=False):
    """Return editable semantic paths only when rendering is preserved.

    Optional source-order references keep interleaved paint/body editable.
    Non-strict diagnostics retain ordered runs and declare them non-exportable;
    they must not be offered as completed two-layer artwork.
    """
    categories = normalize_categories(objects, categories)
    if len(set(layer_names)) != len(layer_names) or set(layer_names) - set(ORDER):
        raise ValueError("未知或重复 SVG 图层。")
    if any(ids for name, ids in categories.items() if name not in layer_names):
        raise ValueError("所选对象的类别未包含在 SVG 图层中。")
    roles = {oid: name for name, ids in categories.items() for oid in ids}
    shared = set(categories['logo']) & set(categories['门徽'])
    selected = [o for o in objects if o['id'] in roles]
    original = svg_document(selected, bounds, width=width)
    root = ET.fromstring(original)
    scene = next((n for n in root if n.tag == f'{{{SVG}}}g'), None)
    scene.set('id', 'source-coordinate-system')
    paths = list(scene)
    by_id = {n.get('id'): n for n in paths}
    for n in paths:
        scene.remove(n)
    for name in layer_names:
        key = CATEGORY_KEYS[name]
        group = ET.SubElement(scene, f'{{{SVG}}}g', {
            'id': 'layer-'+key, 'data-layer': key,
            f'{{{INK}}}groupmode': 'layer', f'{{{INK}}}label': name})
        ET.SubElement(group, f'{{{SVG}}}title').text = name
        for obj in selected:
            if roles[obj['id']] == name:
                node = by_id.get(obj['id'])
                if node is not None:
                    node.set('data-layer', key)
                    node.set('data-source-id', obj['id'])
                    if obj['id'] in shared:
                        node.set('data-shared-categories', 'logo emblem')
                    if name == '车漆':
                        node.set('data-paint-editable', 'fill')
                    group.append(node)
    grouped = _serialize(root)
    ordered_keys = [layer_names.index(roles[o['id']]) for o in selected]
    compatible = ordered_keys == sorted(ordered_keys) or _same_render(original, grouped)
    referenced = False
    if not compatible and source_order_fallback:
        # Keep editable semantic paths in definitions; render references in
        # source order. No duplicate paint geometry or altered source strokes.
        defs = root.find(f'{{{SVG}}}defs')
        if defs is None:
            defs = ET.Element(f'{{{SVG}}}defs')
            root.insert(0, defs)
        for group in list(scene):
            scene.remove(group)
            defs.append(group)
        for obj in selected:
            if obj['id'] in by_id:
                ET.SubElement(scene, f'{{{SVG}}}use', {
                    'href': '#'+obj['id'],
                    '{http://www.w3.org/1999/xlink}href': '#'+obj['id'],
                    'data-source-id': obj['id'], 'data-layer': CATEGORY_KEYS[roles[obj['id']]]})
        referenced = _same_render(original, _serialize(root))
        compatible = referenced
    info = {'schema_version': 1, 'exportable': compatible,
            'shared_logo_emblem_ids': [o['id'] for o in selected if o['id'] in shared],
            'layer_status': 'verified' if compatible else 'layer_order_conflict',
            'rendering': 'source_order_references' if referenced else 'grouped_paths',
            'order_check': ('source_references_render_640_1600' if referenced else
                            'source_order' if ordered_keys == sorted(ordered_keys) else 'render_640_1600'),
            'layers': {CATEGORY_KEYS[n]: {'label': n, 'source_ids': categories[n]} for n in layer_names},
            'physical_scale_verified': False}
    if not compatible:
        if strict:
            raise LayerOrderError('layer_order_conflict：语义分层改变源遮挡关系，需要复核。')
        # Keep contiguous source-order runs; do not claim these are two layers.
        root = ET.fromstring(original)
        scene = next(n for n in root if n.tag == f'{{{SVG}}}g')
        children = list(scene)
        for n in children:
            scene.remove(n)
        last, group = None, None
        for i, node in enumerate(children):
            name = roles[node.get('id')]
            key = CATEGORY_KEYS[name]
            if name != last:
                group = ET.SubElement(scene, f'{{{SVG}}}g', {
                    'id': f'ordered-{key}-{i}', 'data-layer': key})
                ET.SubElement(group, f'{{{SVG}}}title').text = name+'（原序诊断）'
                last = name
            node.set('data-layer', key)
            group.append(node)
    data = dict(metadata or {})
    if data.get('region_name'):
        for name in layer_names:
            key = CATEGORY_KEYS[name]
            label = name+'_'+data['region_name']
            info['layers'][key]['label'] = label
            for group in root.iter(f'{{{SVG}}}g'):
                if group.get('id') == 'layer-'+key:
                    group.set(f'{{{INK}}}label', label)
                    group.find(f'{{{SVG}}}title').text = label
    data.update(info)
    root.find(f'{{{SVG}}}metadata').text = json.dumps(data, ensure_ascii=False, allow_nan=False)
    return _serialize(root), info
