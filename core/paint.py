"""Source paint-style validation and mutually exclusive semantic categories."""
import math
import re

CATEGORY_KEYS = {'车身': 'body', '车漆': 'paint', '彩条': 'stripes',
                 '文字': 'text', 'logo': 'logo', '门徽': 'emblem'}


def hex_color(value):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError("颜色必须是六位 HEX，例如 #1E5AA8。")
    return value.upper()


def style_capability(obj):
    gradient = obj.get('gradient')
    if gradient:
        definition = gradient.get('definition', {})
        appearance = gradient.get('appearance', {})
        stops = definition.get('stops', [])
        if definition.get('kind') not in ('linear', 'radial') or len(stops) < 2:
            return 'unsupported'
        try:
            offsets = [float(s['offset']) for s in stops]
            for stop in stops:
                hex_color(stop['color'])
            values = [*appearance['start'], *appearance['end'], *appearance['matrix']]
            if (len(appearance['start']) != 2 or len(appearance['end']) != 2
                    or len(appearance['matrix']) != 6
                    or not all(math.isfinite(v) for v in values)
                    or not all(math.isfinite(v) and 0 <= v <= 1 for v in offsets)
                    or offsets != sorted(offsets)):
                return 'unsupported'
            if definition['kind'] == 'radial' and appearance['start'] == appearance['end']:
                return 'unsupported'
        except (KeyError, ValueError, TypeError):
            return 'unsupported'
        return 'source_gradient'
    try:
        hex_color(obj.get('fill'))
    except ValueError:
        return 'unsupported'
    if obj.get('fill_status') in ('unsupported_appearance', 'invalid_appearance'):
        return 'unsupported'
    return 'source_solid'


def normalize_categories(objects, categories):
    valid = {o['id'] for o in objects}
    if len(valid) != len(objects):
        raise ValueError('源对象 ID 不唯一。')
    result, claimed = {}, set()
    if set(categories) - set(CATEGORY_KEYS):
        raise ValueError('未知类别。')
    for name in CATEGORY_KEYS:
        ids = categories.get(name, [])
        if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids):
            raise ValueError('类别对象必须是源 ID 数组。')
        if len(ids) != len(set(ids)) or not set(ids) <= valid:
            raise ValueError('类别含未知或重复源对象。')
        if name != '车身':
            if claimed & set(ids):
                raise ValueError('非车身类别存在重叠对象。')
            claimed.update(ids)
        result[name] = list(ids)
    result['车身'] = [i for i in result['车身'] if i not in claimed]
    return result

