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
    return regions


def object_regions(objects, regions, *, partial_ids=()):
    """Assign contained paths, with opt-in dominant overlap for known graphics."""
    memberships = [(r, set(r['selected_ids'])) for r in regions]
    partial_ids = set(partial_ids)
    result = {}
    for obj in objects:
        matches = []
        for region, ids in memberships:
            a, b = region['bbox'], obj.get('bbox')
            # Independently decoded paint panels can extend just past the
            # vehicle anchor (observed drift < 0.01% of its extent). Keep them
            # with their view; ambiguous or materially crossing paths stay out.
            eps = max(a[2]-a[0], a[3]-a[1], 1) * 1e-4
            if obj['id'] in ids or (b and a[0]-eps <= b[0] and a[1]-eps <= b[1]
                                   and b[2] <= a[2]+eps and b[3] <= a[3]+eps):
                matches.append(region['id'])
        if not matches and obj['id'] in partial_ids:
            # A classified stripe may bleed beyond its vehicle anchor. Its
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


def split_by_region(ids, assignments):
    buckets = {}
    for oid in ids:
        buckets.setdefault(assignments.get(oid), []).append(oid)
    return list(buckets.values())
