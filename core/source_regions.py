"""Source-view regions anchored to existing vectors, shared by all categories."""
import re

from geometry import union_bounds


def valid_region_name(name):
    return (isinstance(name, str) and 0 < len(name) <= 40 and name == name.strip(' .')
            and not re.search(r'[<>:"/\\|?*\x00-\x1f]', name))


def validate_region_proposals(regions, allowed_ids, views):
    if not isinstance(regions, list) or len(regions) > 64:
        raise ValueError('原稿区域必须是至多 64 项的列表。')
    seen, names = set(), set()
    for region in regions:
        if not isinstance(region, dict) or set(region) != {'id', 'name', 'source_view', 'anchor_ids'}:
            raise ValueError('原稿区域字段无效。')
        rid, name, anchors = region['id'], region['name'], region['anchor_ids']
        if (not isinstance(rid, str) or not re.fullmatch(r'R[1-9][0-9]{0,2}', rid)
                or rid in seen or not valid_region_name(name) or name in names
                or not isinstance(region['source_view'], str) or region['source_view'] not in views
                or not isinstance(anchors, list) or not anchors
                or any(not isinstance(i, str) or i not in allowed_ids for i in anchors)
                or len(set(anchors)) != len(anchors)):
            raise ValueError('原稿区域包含重复名称、无效方向或未知候选。')
        seen.add(rid)
        names.add(name)


def build_source_regions(candidates, decision):
    by_id = {c['id']: c for c in candidates}
    regions = []
    for proposal in decision.get('source_regions', []):
        anchors = [by_id[i] for i in proposal['anchor_ids']]
        regions.append(dict(proposal, bbox=union_bounds(anchors),
                            selected_ids=list(dict.fromkeys(
                                i for c in anchors for i in c['member_ids']))))
    return normalize_source_regions(regions)


def normalize_source_regions(regions):
    """Coalesce duplicate vehicle anchors, without merging distinct directions.

    Batched labels can select both the full body and its structural parent.
    Require shared source paths and nearly identical extents, or coincident
    bounds. Ordinary spatial overlap alone is not evidence of a duplicate.
    """
    result = []
    generic = {'待确认视图', '整体视图', '混合视图'}
    for original in regions:
        region = dict(original)
        for old in result:
            a, b = old['bbox'], region['bbox']
            widths = [a[2]-a[0], b[2]-b[0]]
            heights = [a[3]-a[1], b[3]-b[1]]
            if min(widths+heights) <= 0:
                continue
            overlap = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(
                0, min(a[3], b[3])-max(a[1], b[1]))
            same_extent = (min(widths)/max(widths) >= .85
                           and min(heights)/max(heights) >= .85
                           and overlap/min(widths[0]*heights[0], widths[1]*heights[1]) >= .98)
            shared = set(old['selected_ids']) & set(region['selected_ids'])
            exact = all(abs(x-y) <= max(widths+heights)*1e-6 for x, y in zip(a, b))
            v1, v2 = old['source_view'], region['source_view']
            if (not same_extent or not (shared or exact)
                    or (v1 != v2 and v1 not in generic and v2 not in generic)):
                continue
            old['selected_ids'] = list(dict.fromkeys(old['selected_ids']+region['selected_ids']))
            old['anchor_ids'] = list(dict.fromkeys(old.get('anchor_ids', [])+region.get('anchor_ids', [])))
            old['bbox'] = union_bounds([old, region])
            if v1 in generic and v2 not in generic:
                old.update(name=region['name'], source_view=v2)
            break
        else:
            result.append(region)
    return result


def object_regions(objects, regions, *, partial_ids=(), paint_ids=()):
    """Assign paths to one view, allowing bounded drift for classified paint."""
    memberships = [(r, set(r['selected_ids'])) for r in regions]
    partial_ids = set(partial_ids)
    paint_ids = set(paint_ids)
    result = {}
    for obj in objects:
        matches = []
        for region, ids in memberships:
            a, b = region['bbox'], obj.get('bbox')
            # All paths retain the original rounding tolerance. Classified
            # paint may extend up to 0.2% of each anchor axis: decoded shell
            # panels can slightly exceed the structural group's bounds.
            # Never use the looser 80% graphic-overlap rule for paint.
            eps = max(a[2]-a[0], a[3]-a[1], 1) * 1e-4
            dx = max(eps, (a[2]-a[0])*.002) if obj['id'] in paint_ids else eps
            dy = max(eps, (a[3]-a[1])*.002) if obj['id'] in paint_ids else eps
            if obj['id'] in ids or (b and a[0]-dx <= b[0] and a[1]-dy <= b[1]
                                   and b[2] <= a[2]+dx and b[3] <= a[3]+dy):
                matches.append(region['id'])
        if not matches and obj['id'] in partial_ids:
            # A classified graphic may bleed beyond its region anchor. Its
            # dominant bounds and centre must identify one view unambiguously.
            # Do not enlarge region bounds or apply this to arbitrary panels.
            b = obj.get('bbox')
            area = (b[2]-b[0])*(b[3]-b[1]) if b else 0
            if area > 0:
                cx, cy = (b[0]+b[2])/2, (b[1]+b[3])/2
                for region, _ in memberships:
                    a = region['bbox']
                    overlap = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(
                        0, min(a[3], b[3])-max(a[1], b[1]))
                    if (a[0] <= cx <= a[2] and a[1] <= cy <= a[3]
                            and overlap/area >= .8):
                        matches.append(region['id'])
        if len(matches) == 1:
            result[obj['id']] = matches[0]
    return result


def region_for_ids(ids, assignments, regions):
    keys = {assignments.get(i) for i in ids}
    if len(keys) == 1 and None not in keys:
        return next((r for r in regions if r['id'] in keys), None)
    return None


def split_by_region(ids, assignments, *, objects=None):
    """Split known views; optionally keep disconnected unknown paint apart.

    Spatial components only separate review items, never infer a direction or
    alter source geometry. Callers without geometry retain legacy grouping.
    """
    ids = list(ids)
    unknown_groups = {}
    if objects is not None:
        bounds = {o['id']: o.get('bbox') for o in objects}
        components = []
        for oid in ids:
            if assignments.get(oid) is not None:
                continue
            box = bounds.get(oid)
            touching = []
            if box:
                for index, group in enumerate(components):
                    for member in group:
                        other = bounds.get(member)
                        if not other:
                            continue
                        eps = max(box[2]-box[0], box[3]-box[1],
                                  other[2]-other[0], other[3]-other[1], 1)*1e-4
                        if (box[0] <= other[2]+eps and other[0] <= box[2]+eps
                                and box[1] <= other[3]+eps and other[1] <= box[3]+eps):
                            touching.append(index)
                            break
            merged = [oid]
            for index in reversed(touching):
                merged.extend(components.pop(index))
            components.append(merged)
        unknown_groups = {oid: index for index, group in enumerate(components) for oid in group}
    buckets = {}
    for oid in ids:
        region = assignments.get(oid)
        key = ('region', region) if region is not None else ('unknown', unknown_groups.get(oid))
        buckets.setdefault(key, []).append(oid)
    return list(buckets.values())


def merge_surface_patterns(category, entries):
    """One body/paint export per confirmed region, unless explicitly split.

    Unknown regions and conflicting directions stay separate. Disconnected
    source paths retain their coordinates; this never unions geometry.
    """
    result = []
    for original in entries:
        entry = dict(original, selected_ids=list(original['selected_ids']))
        if 'keep_separate' in entry and not isinstance(entry['keep_separate'], bool):
            raise ValueError('单独导出标记必须是布尔值。')
        region = entry.get('source_region')
        if category in ('车身', '车漆') and region and not entry.get('keep_separate'):
            owner = next((old for old in result if old.get('source_region') == region
                          and old['source_view'] == entry['source_view']
                          and not old.get('keep_separate')), None)
            if owner is not None:
                owner['selected_ids'] = list(dict.fromkeys(owner['selected_ids']+entry['selected_ids']))
                continue
        result.append(entry)
    return result
