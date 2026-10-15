"""Conservative, colour-independent candidate ranking and FlexiSIGN AGroup detection."""
from __future__ import annotations
import colorsys
import math
from geometry import union_bounds


def rank_candidates(objects, containers=None):
    bounds = union_bounds(objects)
    doc_w = max(bounds[2] - bounds[0], 1e-9)
    doc_h = max(bounds[3] - bounds[1], 1e-9)
    for obj in objects:
        obj['score'] = 0.0
        box = obj['bbox']
        w = box[2] - box[0]
        h = box[3] - box[1]
        if w <= 0 or h <= 0:
            continue
        relative_area = w * h / (doc_w * doc_h)
        if relative_area >= .00005:
            obj['score'] = round(.04 + .12 * min(relative_area / .01, 1) + (.04 if 'C' in obj.get('d', '') else 0), 4)
        fill = obj.get('fill')
        if not fill or len(fill) != 7:
            continue
        r, g, b = [int(fill[i:i+2], 16) / 255 for i in (1, 3, 5)]
        hue, sat, val = colorsys.rgb_to_hsv(r, g, b)
        aspect = w / h
        if aspect > 12 and w > doc_w * .075:
            obj['score'] = max(obj['score'], .4)

        has_curve = 'C' in obj.get('d', '')
        min_sat = 0.08 if (has_curve and aspect >= 1.5) else 0.25
        min_w = doc_w * 0.025 if (has_curve and w > 40) else doc_w * 0.075
        if sat < min_sat or val < 0.20 or aspect < 1.8 or w < min_w or w * h < doc_w * doc_h * 0.0001:
            continue
        detail = obj.get('details', {})
        if detail.get('closed') is False:
            continue
        score = .23 + .18 * sat + .17 * min(aspect / 7, 1) + .20 * min(w / (doc_w * .65), 1)
        if has_curve:
            score += .12
        if detail.get('closed'):
            score += .08
        if obj.get('requires_review_geometry'):
            score -= .08
        obj['score'] = round(max(0, min(.98, score)), 4)
    return bounds


def candidate_agroups(objects, containers, doc_w, doc_h):
    if not containers:
        return []
    agroups = [c for c in containers if c.get('class_name') == 'AGroup']
    if not agroups:
        return []

    scored_groups = []
    for g in agroups:
        s, e = g['source_offset'], g['end_offset']
        members = [o for o in objects if s < o['source_offset'] < e]
        count = len(members)
        if not (2 <= count <= 60):
            continue
        box = union_bounds(members)
        gw = box[2] - box[0]
        gh = box[3] - box[1]
        if gw <= 0 or gh <= 0:
            continue
        aspect = gw / gh
        if gw < 35 or aspect < 1.1:
            continue
        curve_count = sum('C' in o.get('d', '') for o in members)
        if curve_count == 0:
            continue
        curve_ratio = curve_count / count

        fills = [o.get('fill') for o in members if o.get('fill')]
        unique_fills = set(fills)
        chromatic = []
        for c in unique_fills:
            if c and len(c) == 7:
                r, g_val, b = [int(c[i:i+2], 16) / 255.0 for i in (1, 3, 5)]
                h, sat, v = colorsys.rgb_to_hsv(r, g_val, b)
                if sat >= 0.05 and 0.12 <= v <= 0.99:
                    chromatic.append((c, sat, v))

        score = 0.25 * curve_ratio + 0.25 * min(aspect / 4.0, 1.0) + 0.15 * min(gw / (doc_w * 0.35), 1.0)
        if chromatic:
            max_sat = max(x[1] for x in chromatic)
            score += 0.20 * min(max_sat / 0.5, 1.0)
            if len(chromatic) >= 2:
                score += 0.25
        else:
            score -= 0.25

        if score >= 0.40:
            scored_groups.append({
                'group_id': g['object_id'],
                'members': members,
                'member_ids': [o['id'] for o in members],
                'bbox': box,
                'aspect': aspect,
                'colors': list(unique_fills),
                'score': round(score, 3)
            })

    scored_groups.sort(key=lambda x: x['score'], reverse=True)
    dedup = []
    for g in scored_groups:
        g_set = set(g['member_ids'])
        if any(len(g_set.intersection(set(ex['member_ids']))) >= len(g_set) * 0.8 for ex in dedup):
            continue
        dedup.append(g)

    for g in dedup:
        boost = min(0.95, round(g['score'] * 0.9, 4))
        for o in g['members']:
            o['score'] = max(o.get('score', 0), boost)

    native_groups = []
    for g in dedup:
        gw = g['bbox'][2] - g['bbox'][0]
        rank = round(gw * g['score'] * math.sqrt(min(len(g['members']), 6)), 2)
        cols_str = ', '.join(g['colors']) if g['colors'] else 'no-fill'
        native_groups.append({
            'objects': g['members'],
            'bbox': g['bbox'],
            'rank': rank,
            'selected_ids': g['member_ids'],
            'mean_score': g['score'],
            'is_native_group': True,
            'group_id': g['group_id'],
            'label': '原生组合 OID %d (%d条) [%s]' % (g['group_id'], len(g['members']), cols_str)
        })
    return native_groups


def candidate_groups(objects, containers=None):
    bounds = union_bounds(objects)
    doc_w = max(bounds[2] - bounds[0], 1e-9)
    doc_h = max(bounds[3] - bounds[1], 1e-9)

    native_groups = candidate_agroups(objects, containers, doc_w, doc_h)
    used_ids = set()
    for g in native_groups:
        used_ids.update(g['selected_ids'])

    candidates = [o for o in objects if o.get('score', 0) >= 0.55 and o['id'] not in used_ids]
    candidates = sorted(candidates, key=lambda o: o['score'], reverse=True)[:500]
    spatial_groups = []
    for obj in candidates:
        box = obj['bbox']
        w = box[2] - box[0]
        h = box[3] - box[1]
        matches = []
        for group in spatial_groups:
            g = group['bbox']
            gw = g[2] - g[0]
            gh = g[3] - g[1]
            overlap_x = min(g[2], box[2]) - max(g[0], box[0])
            overlap_y = min(g[3], box[3]) - max(g[1], box[1])
            if overlap_x > min(w, gw) * 0.12 and overlap_y >= -max(h, gh) * 0.12:
                matches.append(group)
        if matches:
            g = matches[0]
            g['objects'].append(obj)
            g['bbox'] = union_bounds(g['objects'])
        else:
            spatial_groups.append({'objects': [obj], 'bbox': list(box)})

    for group in spatial_groups:
        b = group['bbox']
        w = b[2] - b[0]
        h = max(b[3] - b[1], 1e-6)
        mean = sum(o['score'] for o in group['objects']) / len(group['objects'])
        group['rank'] = round(w * mean * math.sqrt(min(len(group['objects']), 4)), 2)
        group['selected_ids'] = [o['id'] for o in group['objects']]
        group['mean_score'] = mean
        group['is_native_group'] = False
        group['label'] = '空间聚集 (%d条)' % len(group['objects'])

    all_groups = native_groups + spatial_groups
    all_groups.sort(key=lambda g: (1 if g.get('is_native_group') else 0, round(g['rank'], 1), round(g['bbox'][3], 1)), reverse=True)
    return all_groups
