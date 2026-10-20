"""Six-category recognition with separate coverage for source paint surfaces."""
import bisect
import hashlib
import json
import math
import re
import struct
from pathlib import Path

import olefile
from PIL import Image, ImageDraw, ImageFont

from core.fs_attributes import load_appearances, scan_container_ranges
from core.fs_container import fields, unpack_objects
from core.fs_geometry import extract_geometry
from core.fs_scene import compose_scene
from geometry import svg_document, rasterize, union_bounds
from vector_preview import VectorImage

CATEGORIES = ('车身', '彩条', '文字', 'logo', '门徽', '车漆')


def paint_candidates(objects):
    """Large filled source surfaces are proposals, never automatic paint labels."""
    from core.paint import style_capability
    bounds = union_bounds(objects)
    page_area = max((bounds[2]-bounds[0])*(bounds[3]-bounds[1]), 1e-6)
    result = []
    for obj in objects:
        box = obj['bbox']
        w, h = box[2]-box[0], box[3]-box[1]
        if (min(w, h) <= 1e-5
                or obj.get('details', {}).get('closed') is False
                or style_capability(obj) == 'unsupported'):
            continue
        result.append({'members': [obj], 'bbox': box, 'native_type': 'PaintSurface',
                       'source_span': [obj['source_offset'], obj['source_offset']],
                       'member_ids': [obj['id']], 'aspect': w/h, 'area': w*h})
    all_surfaces = sorted(result, key=lambda c: -c['area'])
    surfaces = [c for c in all_surfaces if c['area'] >= page_area*.001]
    # A side's base can live outside its vehicle group while the roof and
    # mirror housings live inside it. Global area ranking loses those smaller
    # surfaces. Offer regional bundles AND their individual paths for review;
    # matching appearance is only a proposal, never a semantic assignment.
    largest = max((c['area'] for c in surfaces), default=0)
    bundles, details, seen = [], [], set()
    for anchor in surfaces:
        if anchor['area'] < largest * .2:
            continue
        source = anchor['members'][0]
        a = anchor['bbox']
        dx, dy = (a[2]-a[0])*.1, (a[3]-a[1])*.1
        companions = []
        for surface in all_surfaces:
            obj, b = surface['members'][0], surface['bbox']
            # Small roof housings need local scale; globally admitting small
            # white parts would also collect lamps and wheel highlights.
            roof_detail = (surface['area'] >= anchor['area']*.0025
                           and b[1] >= a[3]-dy)
            if (not (surface['area'] >= anchor['area']*.015 or roof_detail)
                    or surface['area'] > anchor['area']*.35
                    or (obj.get('fill') or '').lower() != (source.get('fill') or '').lower()
                    or obj.get('gradient') != source.get('gradient')
                    or not (a[0]-dx <= b[0] and a[1]-dy <= b[1]
                            and b[2] <= a[2]+dx and b[3] <= a[3]+dy)):
                continue
            companions.append(surface)
        if not companions:
            continue
        members = sorted([source] + [c['members'][0] for c in companions],
                         key=lambda o: o['source_offset'])
        ids = tuple(o['id'] for o in members)
        if ids in seen:
            continue
        seen.add(ids)
        box = union_bounds(members)
        w, h = box[2]-box[0], box[3]-box[1]
        bundles.append({'members': members, 'bbox': box, 'native_type': 'PaintRegion',
                        'source_span': [members[0]['source_offset'], members[-1]['source_offset']],
                        'member_ids': list(ids), 'aspect': w/h, 'area': w*h,
                        'paint_anchor_id': source['id']})
        details.extend([anchor] + companions)
    proposals, provided = [], set()
    for c in bundles + details + surfaces:
        key = tuple(c['member_ids'])
        if key not in provided:
            provided.add(key)
            proposals.append(c)
    return proposals


def text_ranges(raw, classes, word):
    result = []
    for cls, name in classes.items():
        if name not in ('AText', 'AArcText'):
            continue
        marker, pos = struct.pack('<I', cls), 0
        while True:
            p = raw.find(marker, pos)
            if p < 0:
                break
            pos = p + 1
            start = p + 4 + word
            end = start + int.from_bytes(raw[p+4:start], 'little')
            if p < 4 or end > len(raw) or end <= start + 30 or raw[end:end+2] != b'\r\x90':
                continue
            try:
                fields(raw[start:end], word=word)
            except ValueError:
                continue
            result.append({'class_name': name, 'source_offset': p, 'end_offset': end})
    return result


def load_scene(source):
    doc = extract_geometry(source)
    word = doc['geometry_stats']['tlv_word_bytes']
    with olefile.OleFileIO(source) as ole:
        leaves, refs = load_appearances(ole, doc['objects'], word)
        raw, _ = unpack_objects(ole.openstream('FlexiSIGN Contents/Page 1/Objects').read())
        containers = scan_container_ranges(raw, refs['classes'], word)
        containers, _ = load_appearances(ole, containers, word)
    for obj in leaves:
        obj.update(id=str(obj['object_id']) + '@' + str(obj['source_offset']), matrix=[1, 0, 0, 1, 0, 0])
    return compose_scene(leaves, containers), containers + text_ranges(raw, refs['classes'], word)


def loose_graphics(objects, native_candidates):
    """Expose loose source curves, with optional same-paint spatial bundles.

    Bundles are proposals for visual classification, never automatic labels.
    Individual curves remain selectable so mixed-colour designs can be assembled
    and a proposal containing structural details can be rejected.
    """
    bounds = union_bounds(objects)
    dw, dh = bounds[2]-bounds[0], bounds[3]-bounds[1]
    owned = {oid for c in native_candidates if len(c['member_ids']) <= 100
             for oid in c['member_ids']}
    paths = []
    for obj in objects:
        x0, y0, x1, y1 = obj['bbox']
        w, h = x1-x0, y1-y0
        if (obj['id'] in owned or 'C' not in obj.get('d', '')
                or obj.get('fill') in (None, 'none')
                or obj.get('details', {}).get('closed') is False
                or w < dw*.05 or h <= 1e-5 or w/h < 1.8 or h > dh*.3):
            continue
        paths.append(obj)

    def proposal(members, kind):
        box = union_bounds(members)
        w, h = box[2]-box[0], box[3]-box[1]
        return {'members': members, 'bbox': box, 'native_type': kind,
                'source_span': [min(o['source_offset'] for o in members),
                                max(o['source_offset'] for o in members)],
                'member_ids': [o['id'] for o in members], 'aspect': w/h, 'area': w*h}

    remaining = list(paths)
    bundles = []
    while remaining:
        cluster = [remaining.pop(0)]
        changed = True
        while changed:
            changed = False
            for obj in remaining[:]:
                a = obj['bbox']
                for other in cluster:
                    b = other['bbox']
                    overlap_x = min(a[2], b[2])-max(a[0], b[0])
                    overlap_y = min(a[3], b[3])-max(a[1], b[1])
                    if (obj['fill'] == other['fill']
                            and overlap_x > min(a[2]-a[0], b[2]-b[0])*.15
                            and overlap_y >= -max(a[3]-a[1], b[3]-b[1])*.1):
                        cluster.append(obj)
                        remaining.remove(obj)
                        changed = True
                        break
        if len(cluster) > 1:
            bundles.append(proposal(cluster, 'SpatialPaths'))
    bundles.sort(key=lambda c: -c['area'])
    return bundles + sorted((proposal([o], 'SourcePath') for o in paths),
                            key=lambda c: -c['area'])


def emblem_candidates(native_candidates):
    """Expose complete arc badges and their nested graphics before area caps.

    These are native source groups, not colour-based automatic labels. Keep
    maximal nested graphics inside the arc envelope, excluding technical text
    underneath it and vehicle-sized parents.
    """
    result, seen = [], set()
    for arc in native_candidates:
        if arc['native_type'] != 'AArcText':
            continue
        a = arc['bbox']
        aw, ah = a[2]-a[0], a[3]-a[1]
        arc_ids = set(arc['member_ids'])
        graphics = []
        for c in native_candidates:
            b = c['bbox']
            if (c['native_type'] not in ('AGroup', 'AComposite')
                    or arc_ids.intersection(c['member_ids'])
                    or not (a[0] <= b[0] and a[1] <= b[1] and b[2] <= a[2] and b[3] <= a[3])
                    or b[2]-b[0] > aw*.65 or b[3]-b[1] > ah*.8
                    or c['area'] < arc['area']*.005):
                continue
            graphics.append(c)
        maximal = [c for c in graphics if not any(
            set(c['member_ids']) < set(other['member_ids']) for other in graphics)]
        logo_members = {o['id']: o for c in maximal for o in c['members']}
        if not logo_members:
            continue
        for kind, members in (
                ('DoorEmblem', {o['id']: o for o in arc['members']} | logo_members),
                ('EmblemLogo', logo_members)):
            members = sorted(members.values(), key=lambda o: o['source_offset'])
            ids = tuple(o['id'] for o in members)
            if ids in seen:
                continue
            seen.add(ids)
            box = union_bounds(members)
            w, h = box[2]-box[0], box[3]-box[1]
            result.append({'members': members, 'bbox': box, 'native_type': kind,
                           'source_span': [members[0]['source_offset'], members[-1]['source_offset']],
                           'member_ids': list(ids), 'aspect': w/h, 'area': w*h,
                           'arc_member_ids': list(arc['member_ids']),
                           'logo_member_ids': list(logo_members)})
    return result


def wordmark_candidates(native_candidates):
    """Split native text children from adjacent native graphic groups.

    These are proposals for visual classification, not colour/filename rules.
    Characters converted into compound paths stay whole inside native AText.
    """
    result, seen = [], set()
    for parent in native_candidates:
        if parent['native_type'] != 'AGroup' or len(parent['members']) > 200:
            continue
        parent_ids = set(parent['member_ids'])
        texts = [c for c in native_candidates if c['native_type']=='AText'
                 and set(c['member_ids']) < parent_ids]
        text_members = {o['id']: o for c in texts for o in c['members']}
        if not text_members:
            continue
        t = union_bounds(list(text_members.values()))
        th = t[3]-t[1]
        if th <= 0 or t[2]-t[0] < th*1.5 or parent['bbox'][3]-parent['bbox'][1] > th*3:
            continue
        remaining = parent_ids-set(text_members)
        graphics = []
        for c in native_candidates:
            b = c['bbox']
            side_gap = max(t[0]-b[2], b[0]-t[2])
            if (c['native_type'] in ('AGroup', 'AComposite')
                    and set(c['member_ids']) <= remaining
                    and -.05*th <= side_gap <= th*2
                    and min(t[3], b[3])-max(t[1], b[1]) > 0
                    and c['area'] >= th*th*.05):
                graphics.append(c)
        maximal = [c for c in graphics if not any(
            set(c['member_ids']) < set(other['member_ids']) for other in graphics)]
        if not maximal:
            continue
        proposals = [('WordmarkText', list(text_members.values()))]
        proposals += [('WordmarkLogo', c['members']) for c in maximal]
        for kind, members in proposals:
            members = sorted(members, key=lambda o:o['source_offset'])
            ids = tuple(o['id'] for o in members)
            if ids in seen:
                continue
            seen.add(ids)
            box = union_bounds(members)
            w, h = box[2]-box[0], box[3]-box[1]
            result.append({'members': members, 'bbox': box, 'native_type': kind,
                           'source_span': [members[0]['source_offset'], members[-1]['source_offset']],
                           'member_ids': list(ids), 'aspect': w/h, 'area': w*h})
    return result


def build_candidates(objects, containers, limit=120):
    """Keep native ownership, deduplicate nested copies and expose omissions."""
    objects = sorted(objects, key=lambda o: o['source_offset'])
    offsets = [o['source_offset'] for o in objects]
    candidates, seen = [], set()
    for group in containers:
        if group['class_name'] not in ('AGroup', 'AComposite', 'AText', 'AArcText'):
            continue
        lo = bisect.bisect_left(offsets, group['source_offset'])
        hi = bisect.bisect_left(offsets, group['end_offset'])
        members = objects[lo:hi]
        ids = tuple(o['id'] for o in members)
        meaningful_compound = len(members) == 1 and members[0].get('d','').count('M') >= 3
        if (len(members) < 2 and not meaningful_compound) or ids in seen:
            continue
        seen.add(ids)
        bounds = union_bounds(members)
        w, h = bounds[2]-bounds[0], bounds[3]-bounds[1]
        if min(w, h) < 1e-5:
            continue
        candidates.append({'members': members, 'bbox': bounds, 'native_type': group['class_name'],
                           'source_span': [group['source_offset'], group['end_offset']],
                           'member_ids': list(ids), 'aspect': w/h, 'area': w*h})
    # Reserve coverage for ungrouped graphics as well as native ownership.
    loose = loose_graphics(objects, candidates)
    max_area = max((c['area'] for c in candidates), default=1)
    paint = paint_candidates(objects)
    badges = emblem_candidates(candidates)
    wordmarks = wordmark_candidates(candidates)
    buckets = [[], [], badges+wordmarks, loose, paint]
    for c in candidates:
        body = 2.2 < c['aspect'] < 5.5 and c['area'] > max_area * .025 and len(c['members']) > 100
        bucket = 0 if body else 1 if c['native_type'] in ('AText','AArcText') else 2
        buckets[bucket].append(c)
    selected = []
    leftovers = []
    caps = [limit//6]*4 + [limit-4*(limit//6)]
    for index, (bucket, cap) in enumerate(zip(buckets, caps)):
        if index not in (3, 4):
            bucket.sort(key=lambda c: (c['native_type'] not in ('DoorEmblem', 'EmblemLogo', 'WordmarkText', 'WordmarkLogo'),
                                       -c['area'], len(c['members'])))
        local = []
        for c in bucket:
            ids = set(c['member_ids'])
            if any(len(ids & set(other['member_ids'])) / max(len(ids), len(other['member_ids'])) > .92
                   for other in local):
                continue
            local.append(c)
        selected.extend(local[:cap])
        leftovers.extend(local[cap:])
    selected.extend(leftovers[:max(0, limit-len(selected))])
    for i, c in enumerate(selected):
        c.update(id=f'G{i+1:03d}', score=1, fill='none')
    return selected, {'native_candidates': len(candidates), 'loose_graphic_candidates': len(loose),
                      'provided_emblem_candidates': sum(c['native_type'] in ('DoorEmblem', 'EmblemLogo') for c in selected),
                      'provided_wordmark_candidates': sum(c['native_type'] in ('WordmarkText', 'WordmarkLogo') for c in selected),
                      'paint_surface_candidates': sum(c['native_type']=='PaintSurface' for c in paint),
                      'paint_region_candidates': sum(c['native_type']=='PaintRegion' for c in paint),
                      'provided_paint_surfaces': sum(c['native_type']=='PaintSurface' for c in selected),
                      'provided_paint_regions': sum(c['native_type']=='PaintRegion' for c in selected),
                      'provided_candidates': len(selected),
                      'omitted_or_deduplicated_candidates': len(candidates)+len(badges)+len(wordmarks)+len(loose)+len(paint)-len(selected),
                      'candidate_limit': limit, 'reference_library_used': False}


def candidate_atlas(candidates, path):
    columns, tw, th = 4, 400, 190
    sheet = Image.new('RGB', (columns*tw, max(1, math.ceil(len(candidates)/columns))*th), '#e5e7eb')
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 16)
    for i, c in enumerate(candidates):
        x, y = i % columns * tw, i // columns * th
        svg = svg_document(c['members'], width=380, background='#bbbbbb')
        svg = re.sub(r'(<svg\b[^>]*\bheight=")[^"]*', r'\g<1>150', svg, count=1)
        im, _, _ = VectorImage(svg).render(1, (0, 0), (380, 150))
        tile = Image.new('RGBA', im.size, '#bbbbbb')
        tile.alpha_composite(im)
        sheet.paste(tile.convert('RGB'), (x+10, y+5))
        draw.text((x+10, y+158), f"{c['id']} | {c['native_type']} | {len(c['members'])} paths", font=font, fill='black')
    sheet.save(path)


def prepare(source, directory, limit=120):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    objects, containers = load_scene(source)
    candidates, coverage = build_candidates(objects, containers, limit)
    candidate_atlas(candidates, directory/'group_atlas.png')
    rasterize(svg_document(objects, width=1800, background='#ffffff'), directory/'source_scene.png')
    return objects, candidates, coverage


def recognize_groups(candidates, directory, config):
    from vision import recognize
    directory = Path(directory)
    metadata = [{k: c[k] for k in ('id', 'bbox', 'native_type', 'source_span')} |
                {'path_count': len(c['member_ids'])} for c in candidates]
    return recognize({'objects': candidates, 'component_mode': True, 'group_metadata': metadata},
                     directory/'source_scene.png', directory/'group_atlas.png',
                     dict(config, max_candidates=max(len(candidates), 1)))


def resolve_categories(objects, candidates, decision):
    """Map groups to source paths; allow only explicit emblem/logo sharing."""
    by_group = {c['id']: c for c in candidates}
    categories = {}
    for name in CATEGORIES:
        categories[name] = set()
        for gid in decision['categories'][name]:
            categories[name].update(by_group[gid]['member_ids'])
    # Remove technical lettering from broad parents, while retaining the
    # complete badge's graphic when that complete candidate is selected.
    arcs = [set(c['member_ids']) for c in candidates if c['native_type']=='AArcText'
            and set(c['member_ids']) <= categories['门徽']]
    if arcs:
        complete = [set(c['member_ids']) for c in candidates if c['native_type']=='DoorEmblem'
                    and set(c['member_ids']) <= categories['门徽']]
        categories['门徽'] = set().union(*arcs, *complete)
    from core.paint import normalize_categories
    return normalize_categories(objects, {
        name: [o['id'] for o in objects if o['id'] in ids] for name, ids in categories.items()})


def run(source, directory, config, *, use_ai=True):
    source, directory = Path(source), Path(directory)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    objects, candidates, coverage = prepare(source, directory, min(config['max_candidates'], 120))
    decision = (recognize_groups(candidates, directory, config) if use_ai else
                {'categories': {name: [] for name in CATEGORIES},
                 'reason': '手工复核模式，尚未分类；未调用 AI。', 'confidence': 0.0})
    categories = resolve_categories(objects, candidates, decision)
    report = {'source': str(source.resolve()), 'source_sha256': digest, 'coverage': coverage,
              'decision': decision, 'categories': categories, 'status': 'review', 'views': []}
    report['candidates'] = review_candidates(candidates, decision)
    from core.source_regions import build_source_regions
    report['source_regions'] = build_source_regions(candidates, decision)
    for name, ids in categories.items():
        selected = set(ids)
        from core.svg_layers import layered_svg
        svg, _ = layered_svg([o for o in objects if o['id'] in selected], {name: ids},
                             width=2000, layer_names=(name,),
                             metadata={'source_sha256': digest, 'human_reviewed': False})
        svg_path, png_path = directory/(name+'.svg'), directory/(name+'.png')
        svg_path.write_text(svg, encoding='utf8')
        rasterize(svg, png_path, max_width=2000)
        report['views'].append({'label': name+(' · AI 候选' if ids else ' · 未识别，可手动补选'), 'category': name, 'selected_ids': ids,
                                'bbox': union_bounds([o for o in objects if o['id'] in selected]),
                                'svg': str(svg_path.resolve()), 'png': str(png_path.resolve())})
    if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
        raise ValueError('处理期间源文件变化')
    report['source_unchanged'] = True
    (directory/'components_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    return objects, report


def review_candidates(candidates, decision):
    """Compact source memberships for lazy visual review, without new geometry."""
    largest = max((c['area'] for c in candidates), default=1)
    widest = max((c['bbox'][2]-c['bbox'][0] for c in candidates), default=1)
    result = []
    for c in candidates:
        recommended = [name for name, groups in decision['categories'].items() if c['id'] in groups]
        hints = list(recommended)
        if not hints:
            if c['native_type'] in ('PaintSurface', 'PaintRegion'):
                hints = ['车漆']
            elif c['native_type'] in ('AArcText', 'DoorEmblem'):
                hints = ['门徽']
            elif c['native_type'] == 'EmblemLogo':
                hints = ['logo', '门徽']
            elif c['native_type'] == 'WordmarkLogo':
                hints = ['logo']
            elif c['native_type'] == 'WordmarkText':
                hints = ['文字']
            elif c['native_type'] == 'AText':
                hints = ['文字']
            elif c['area'] > largest*.025 and 2.2 < c['aspect'] < 5.5 and len(c['member_ids']) > 100:
                hints = ['车身']
            elif len(c['member_ids']) < 150:
                hints = ['logo']
                if c['aspect'] > 2 and c['bbox'][2]-c['bbox'][0] > widest*.12:
                    hints.append('彩条')
        result.append({'id':c['id'], 'label':c['id']+' · '+c['native_type'],
                       'selected_ids':list(c['member_ids']), 'bbox':c['bbox'],
                       'source_view': decision.get('source_views', {}).get(c['id'], '待确认视图'),
                       'category_hints':hints, 'recommended_categories':recommended})
    return result


def recover_review_candidates(result):
    """Older saved tasks can expose alternative source groups without AI calls."""
    source = Path(result['source'])
    if hashlib.sha256(source.read_bytes()).hexdigest() != result['source_sha256']:
        raise ValueError('源 FS 已变化，请重新提取。')
    _, containers = load_scene(source)
    objects = sorted(result['objects'], key=lambda o:o['source_offset'])
    candidates, _ = build_candidates(objects, containers)
    if hashlib.sha256(source.read_bytes()).hexdigest() != result['source_sha256']:
        raise ValueError('整理候选期间源 FS 发生变化。')
    return review_candidates(candidates, {'categories':{name:[] for name in CATEGORIES}})
