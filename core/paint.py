"""Source style validation and category ownership, including emblem logos."""
import math
import re

CATEGORY_KEYS = {'车身': 'body', '车漆': 'paint', '彩条': 'stripes',
                 '文字': 'text', 'logo': 'logo', '门徽': 'emblem'}


class CategoryOverlapError(ValueError):
    """Contradictory source ownership, with fixed local diagnostic text."""
    def __init__(self, categories):
        self.conflicts = []
        names = [name for name in CATEGORY_KEYS if name != '车身']
        for index, first in enumerate(names):
            for second in names[index+1:]:
                if {first, second} == {'logo', '门徽'}:
                    continue
                shared = set(categories[first]) & set(categories[second])
                if shared:
                    self.conflicts.append({'categories': [first, second], 'source_ids': sorted(shared)})
        detail = '、'.join('/'.join(c['categories'])+f"（{len(c['source_ids'])} 条）" for c in self.conflicts)
        super().__init__('非车身类别存在重叠对象：'+detail+'；仅 logo 与门徽可共享源对象。')


def category_conflicts(categories, *, include_body=False):
    """Only a logo embedded in a door emblem may share nonbody source IDs."""
    owners = {}
    for name, ids in categories.items():
        if name == '车身' and not include_body:
            continue
        for oid in ids or []:
            owners.setdefault(oid, set()).add(name)
    return any(len(names) > 1 and names != {'logo', '门徽'} for names in owners.values())


def category_exclusions(name, categories):
    """Refine broad source parents without sharing logo paths with text."""
    if name == '文字':
        return set(categories.get('logo') or [])
    if name == '车身':
        return {oid for key, ids in categories.items() if key != '车身' for oid in ids or []}
    return set()


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
            claimed.update(ids)
        result[name] = list(ids)
    result['文字'] = [i for i in result['文字'] if i not in category_exclusions('文字', result)]
    if category_conflicts(result):
        raise CategoryOverlapError(result)
    result['车身'] = [i for i in result['车身'] if i not in claimed]
    return result

