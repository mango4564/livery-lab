"""Group-first five-category recognition. AI selects source IDs, never draws."""
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

CATEGORIES = ('车身', '彩条', '文字', 'logo', '门徽')


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


def build_candidates(objects, containers, limit=120):
    """Keep native ownership, deduplicate nested copies and expose omissions."""
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
    # Broad coverage: whole bodies, native text, and smaller graphic groups.
    max_area = max((c['area'] for c in candidates), default=1)
    buckets = [[], [], []]
    for c in candidates:
        body = 2.2 < c['aspect'] < 5.5 and c['area'] > max_area * .025 and len(c['members']) > 100
        bucket = 0 if body else 1 if c['native_type'] in ('AText','AArcText') else 2
        buckets[bucket].append(c)
    selected = []
    for bucket, cap in zip(buckets, (limit//4, limit//4, limit//2)):
        bucket.sort(key=lambda c: (-c['area'], len(c['members'])))
        local = []
        for c in bucket:
            ids = set(c['member_ids'])
            if any(len(ids & set(other['member_ids'])) / max(len(ids), len(other['member_ids'])) > .92
                   for other in local):
                continue
            local.append(c)
        selected.extend(local[:cap])
    for i, c in enumerate(selected):
        c.update(id=f'G{i+1:03d}', score=1, fill='none')
    return selected, {'native_candidates': len(candidates), 'provided_candidates': len(selected),
                      'omitted_or_deduplicated_candidates': len(candidates)-len(selected), 'candidate_limit': limit}


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
    """Map semantic groups back to source paths; never silently double assign."""
    by_group = {c['id']: c for c in candidates}
    categories = {}
    for name in CATEGORIES:
        categories[name] = set()
        for gid in decision['categories'][name]:
            categories[name].update(by_group[gid]['member_ids'])
    # A native arc-text child is more precise than a parent containing seat
    # counts or other technical lettering. Keep only complete contained arcs.
    arcs = [set(c['member_ids']) for c in candidates if c['native_type']=='AArcText'
            and set(c['member_ids']) <= categories['门徽']]
    if arcs:
        categories['门徽'] = set().union(*arcs)
    nonbody = set()
    for name in CATEGORIES[1:]:
        if nonbody & categories[name]:
            raise ValueError('非车身类别存在重叠源对象，需人工复核分组。')
        nonbody.update(categories[name])
    categories['车身'] -= nonbody
    return {name: [o['id'] for o in objects if o['id'] in ids] for name, ids in categories.items()}


def run(source, directory, config):
    source, directory = Path(source), Path(directory)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    objects, candidates, coverage = prepare(source, directory, min(config['max_candidates'], 120))
    decision = recognize_groups(candidates, directory, config)
    categories = resolve_categories(objects, candidates, decision)
    report = {'source': str(source.resolve()), 'source_sha256': digest, 'coverage': coverage,
              'decision': decision, 'categories': categories, 'status': 'review', 'views': []}
    report['candidates'] = review_candidates(candidates, decision)
    for name, ids in categories.items():
        selected = set(ids)
        svg = svg_document([o for o in objects if o['id'] in selected], width=2000)
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
            if c['native_type'] == 'AArcText':
                hints = ['门徽']
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
