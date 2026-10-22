"""Six-category recognition with separate coverage for source paint surfaces."""
import bisect
import colorsys
import hashlib
import json
import math
import re
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from functools import wraps
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


# Only request waits overlap. Image preparation retains its serial execution.
_BATCH_RENDER_LOCK = threading.RLock()


def _serial_render(function):
    @wraps(function)
    def locked(*args, **kwargs):
        with _BATCH_RENDER_LOCK:
            return function(*args, **kwargs)
    return locked


def _ordered_batches(batches, config, prepare, classify):
    """Bounded request overlap with deterministic input-order consumption.

    Prepare on the caller thread. Workers return isolated validated decisions;
    callers retain ownership of merging. Exceptions cancel pending work and
    propagate, never turning failed requests into negative labels.
    """
    from vision import tuning_options
    workers = tuning_options(config)['component_concurrency']
    if workers == 1:
        for number, batch in enumerate(batches, 1):
            local = prepare(number, batch)
            yield batch, local, classify(batch, local)
        return
    pending = deque()
    iterator = iter(enumerate(batches, 1))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='livery-vision') as pool:
        def submit_next():
            item = next(iterator, None)
            if item is None:
                return
            number, batch = item
            local = prepare(number, batch)
            pending.append((batch, local, pool.submit(classify, batch, local)))
        try:
            for _ in range(workers):
                submit_next()
            while pending:
                batch, local, future = pending.popleft()
                response = future.result()
                yield batch, local, response
                submit_next()
        finally:
            for _, _, future in pending:
                future.cancel()


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
    # Reserve spatial coverage before repeated windows and nested surfaces
    # consume the paint budget. Front/rear bases are smaller than side bases.
    coverage_surfaces = []
    for surface in surfaces:
        box = surface['bbox']
        covered = False
        for primary in coverage_surfaces:
            a = primary['bbox']
            overlap = max(0, min(a[2], box[2])-max(a[0], box[0])) * max(
                0, min(a[3], box[3])-max(a[1], box[1]))
            if overlap/surface['area'] >= .8:
                covered = True
                break
        if not covered:
            coverage_surfaces.append(dict(surface, coverage_anchor=True))
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
    for c in coverage_surfaces + bundles + details + surfaces:
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
        if (obj['id'] in owned or not obj.get('d')
                or obj.get('fill') in (None, 'none')
                or obj.get('details', {}).get('closed') is False
                or w < dw*.003 or h <= 1e-5 or h > dh*.3):
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
    # Bounding boxes of curved stripes contain large empty areas. A matching
    # colour inside that box is not evidence that a patch belongs to the stripe.
    import skia
    shapes = {}

    def shape_neighbours(first, second):
        a, b = first['bbox'], second['bbox']
        widths = (a[2]-a[0], b[2]-b[0])
        distance = max(widths)*.03
        if min(widths) < max(widths)*.15:
            # Short companions must meet the design, not merely sit near it.
            distance = min(widths)*.1
        try:
            for obj in (first, second):
                if obj['id'] not in shapes:
                    path = VectorImage._path(obj['d'])
                    path.transform(VectorImage._matrix('matrix('+','.join(
                        str(v) for v in obj.get('matrix', [1, 0, 0, 1, 0, 0]))+')'))
                    shapes[obj['id']] = path
            expanded = skia.Path()
            skia.Paint(Style=skia.Paint.kStrokeAndFill_Style,
                       StrokeWidth=max(distance*2, 1e-5)).getFillPath(shapes[first['id']], expanded)
            return not skia.Op(expanded, shapes[second['id']],
                               skia.PathOp.kIntersect_PathOp).isEmpty()
        except (ValueError, RuntimeError):
            # Leave unsupported geometry out of inferred bundles; the source
            # remains available for manual selection.
            return False

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
                            and overlap_x >= -max(a[2]-a[0], b[2]-b[0])*.03
                            and overlap_y >= -max(a[3]-a[1], b[3]-b[1])*.1
                            and shape_neighbours(obj, other)):
                        cluster.append(obj)
                        remaining.remove(obj)
                        changed = True
                        break
        if (len(cluster) > 1 and any(
                o['bbox'][2]-o['bbox'][0] >= dw*.05 and
                (o['bbox'][2]-o['bbox'][0]) / (o['bbox'][3]-o['bbox'][1]) >= 1.8 for o in cluster)):
            bundles.append(proposal(cluster, 'SpatialPaths'))
    bundles.sort(key=lambda c: -c['area'])
    bundled_ids = {oid for c in bundles for oid in c['member_ids']}
    singles = sorted((proposal([o], 'SourcePath') for o in paths
                             if o['bbox'][2]-o['bbox'][0] >= dw*.05
                             and (o['bbox'][2]-o['bbox'][0]) / (o['bbox'][3]-o['bbox'][1]) >= 1.8),
                            key=lambda c: -c['area'])
    # Reserve coverage before offering the individual children of bundles.
    # Otherwise side-view subpaths displace smaller front/rear decorations.
    return bundles + [c for c in singles if c['member_ids'][0] not in bundled_ids] + [
        c for c in singles if c['member_ids'][0] in bundled_ids]


def emblem_candidates(native_candidates):
    """Expose complete arc badges and their nested graphics before area caps.

    These are native source groups, not colour-based automatic labels. Keep
    maximal nested graphics and complete text lines inside the arc envelope,
    excluding technical text underneath it and vehicle-sized parents.
    """
    result, seen = [], set()
    all_text_ids = {i for c in native_candidates
                    if c['native_type'] in ('AText', 'AArcText') for i in c['member_ids']}
    for arc in native_candidates:
        if arc['native_type'] != 'AArcText':
            continue
        a = arc['bbox']
        aw, ah = a[2]-a[0], a[3]-a[1]
        arc_ids = set(arc['member_ids'])
        # Prefer the smallest compact owner. Coincident text from separate
        # source groups must not be assembled into the same badge.
        owners = [c for c in native_candidates if c['native_type'] == 'AGroup'
                  and arc_ids < set(c['member_ids'])
                  and c['bbox'][2]-c['bbox'][0] <= aw*1.1
                  and c['bbox'][3]-c['bbox'][1] <= max(ah*1.8, aw)]
        owner_ids = set(min(owners, key=lambda c: len(c['member_ids']))['member_ids']) if owners else None
        captions = [c for c in native_candidates
                    if c['native_type'] == 'AText'
                    and not arc_ids.intersection(c['member_ids'])
                    and (owner_ids is None or set(c['member_ids']) <= owner_ids)
                    and a[0] <= c['bbox'][0]
                    and (a[1] <= c['bbox'][1] or (owner_ids is not None
                         and a[1]-c['bbox'][1] <= aw*.15))
                    and c['bbox'][2] <= a[2] and c['bbox'][3] <= a[3]
                    and c['bbox'][2]-c['bbox'][0] <= aw*.95
                    and c['bbox'][3]-c['bbox'][1] <= ah*.4]
        caption_members = {o['id']: o for c in captions for o in c['members']}
        graphics = []
        for c in native_candidates:
            b = c['bbox']
            inside = a[0] <= b[0] and a[1] <= b[1] and b[2] <= a[2] and b[3] <= a[3]
            # An upper arc can sit above its emblem rather than enclosing it.
            # Offer only a compact native group directly below the arc span;
            # the visual classifier still decides whether it is a badge.
            below = (a[0] <= b[0] and b[2] <= a[2]
                     and 0 <= a[1]-b[3] <= aw*.15 and b[1] >= a[1]-aw*.8)
            if (c['native_type'] not in ('AGroup', 'AComposite')
                    or arc_ids.intersection(c['member_ids'])
                    or (owner_ids is not None and not set(c['member_ids']) <= owner_ids)
                    or not (inside or below)
                    or b[2]-b[0] > aw*.65 or b[3]-b[1] > max(ah*.8, aw*.6)
                    or c['area'] < arc['area']*.005):
                continue
            graphics.append(c)
        maximal = [c for c in graphics if not any(
            set(c['member_ids']) < set(other['member_ids']) for other in graphics)]
        # Composite glyphs inside AText are text, even when they resemble a
        # small native graphic group. Keep the entire line, including digits.
        logo_members = {o['id']: o for c in maximal for o in c['members']
                        if o['id'] not in all_text_ids}
        if not logo_members and not caption_members:
            continue
        for kind, members in (
                ('DoorEmblem', {o['id']: o for o in arc['members']} | logo_members | caption_members),
                ('EmblemLogo', logo_members)):
            if not members:
                continue
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
                           'text_member_ids': list(caption_members),
                           'logo_member_ids': list(logo_members)})
    return result


def text_block_candidates(native_candidates):
    """Keep native multiline lettering, including captions converted to paths.

    Reserve complete compact owners for visual classification. Never infer
    missing letters, mirror the other side, or join unrelated spatial groups.
    """
    result, seen = [], set()
    arc_ids = {i for c in native_candidates if c['native_type'] == 'AArcText'
               for i in c['member_ids']}
    for text in native_candidates:
        if text['native_type'] != 'AText':
            continue
        t = text['bbox']
        tw, th = t[2]-t[0], t[3]-t[1]
        if th <= 0 or tw < th*3:
            continue
        ids = set(text['member_ids'])
        owners = sorted((c for c in native_candidates if c['native_type'] == 'AGroup'
                         and ids < set(c['member_ids']) and len(c['members']) <= 200),
                        key=lambda c: len(c['members']))
        for owner in owners:
            if arc_ids.intersection(owner['member_ids']):
                continue
            rest = [o for o in owner['members'] if o['id'] not in ids]
            if len(rest) < 4:
                continue
            b = union_bounds(rest)
            bw, bh = b[2]-b[0], b[3]-b[1]
            overlap = max(0, min(t[2], b[2])-max(t[0], b[0]))
            gap = max(t[1]-b[3], b[1]-t[3])
            if not (.45*tw <= bw <= 1.2*tw and 0 < bh <= th*.8
                    and overlap >= min(tw, bw)*.85 and 0 <= gap <= th*.65):
                continue
            key = tuple(owner['member_ids'])
            if key not in seen:
                result.append(dict(owner, native_type='TextBlock'))
                seen.add(key)
            break
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


def attach_native_contexts(selected, native):
    """Keep the smallest complete native group around each partial proposal."""
    groups = [(c, set(c['member_ids'])) for c in native
              if c['native_type'] == 'AGroup' and 2 <= len(c['member_ids']) <= 80]
    for candidate in selected:
        ids = set(candidate['member_ids'])
        owners = [c for c, members in groups if ids < members]
        if owners:
            owner = min(owners, key=lambda c: (len(c['member_ids']), c['area']))
            candidate['native_context'] = {k: owner[k] for k in
                                          ('member_ids', 'bbox', 'area', 'aspect', 'source_span')}


def build_candidates(objects, containers, limit=120):
    """Keep native ownership, deduplicate nested copies and expose omissions."""
    objects = sorted(objects, key=lambda o: o['source_offset'])
    offsets = [o['source_offset'] for o in objects]
    candidates, seen = [], set()
    # A text container may have exactly the same paths as an enclosing group.
    # Preserve its native text identity before deduplicating memberships.
    for group in sorted(containers, key=lambda g: (
            {'AArcText': 0, 'AText': 1}.get(g['class_name'], 2), g['source_offset'])):
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
    text_blocks = text_block_candidates(candidates)
    # Compact graphics, including gradients and unlettered marks, otherwise
    # lose to vehicle parts in area ranking. Preserve separate placements.
    # This only reserves visual proposals; the model must classify them.
    details = []
    for c in candidates:
        if c['native_type'] != 'AGroup' or not 2 <= len(c['members']) <= 80:
            continue
        if (3 <= len(c['members']) <= 40 and .8 < c['aspect'] < 1.25
                and c['area'] < max_area*.005
                and any(o.get('d', '').count('C') >= 3 for o in c['members'])):
            # Include neutral badges for visual review as well as colourful
            # marks. This pool also contains hardware: never auto-label it.
            details.append(dict(c, native_type='CompactGraphic'))
        colors = set()
        for obj in c['members']:
            fills = [obj.get('fill') or '']
            fills.extend(stop.get('color', '') for stop in
                         (obj.get('gradient') or {}).get('definition', {}).get('stops', []))
            for color in fills:
                if re.fullmatch(r'#[0-9a-fA-F]{6}', color):
                    rgb = bytes.fromhex(color[1:])
                    if max(rgb)-min(rgb) > 45:
                        colors.add(color.lower())
        hues = [colorsys.rgb_to_hsv(*(v/255 for v in bytes.fromhex(color[1:])))[0]
                for color in colors]
        distinct_hues = any(min(abs(a-b), 1-abs(a-b)) >= 1/9
                            for a in hues for b in hues)
        if (distinct_hues and (c['area'] < max_area*.01
                              or (c['area'] < max_area*.75 and c['aspect'] < 3))
                and .5 < c['aspect'] < 8):
            details.append(dict(c, native_type='DetailGraphic'))
    buckets = [[], [], badges+text_blocks+wordmarks+details, loose, paint]
    for c in candidates:
        # Reserve complete source groups before generated surface/detail pools.
        # End views and multi-part decorations can have fewer than 100 paths;
        # neither their orientation nor colour count measures completeness.
        large_group = (c['native_type'] == 'AGroup'
                       and .4 < c['aspect'] < 5.5 and c['area'] > max_area * .025)
        bucket = 0 if large_group else 1 if c['native_type'] in ('AText','AArcText') else 2
        buckets[bucket].append(c)
    selected = []
    leftovers = []
    priority_types = ('DoorEmblem', 'EmblemLogo', 'TextBlock', 'WordmarkText', 'WordmarkLogo',
                      'DetailGraphic', 'CompactGraphic')
    caps = [limit//6]*4 + [limit-4*(limit//6)]
    for index, (bucket, cap) in enumerate(zip(buckets, caps)):
        if index not in (3, 4):
            bucket.sort(key=lambda c: (c['native_type'] not in priority_types,
                                       -c['area'], -len(c['members']) if c['native_type']=='AGroup'
                                       else len(c['members'])))
        local = []
        for c in bucket:
            ids = set(c['member_ids'])
            # A near-identical child can still omit fine outlines. Only an
            # actually contained membership is redundant; for equal bounds
            # sort the complete parent ahead of its partial children.
            if any((ids <= set(other['member_ids'])
                    if c['native_type'] == other['native_type'] == 'AGroup'
                    else len(ids & set(other['member_ids'])) /
                         max(len(ids), len(other['member_ids'])) > .92)
                   for other in local):
                continue
            local.append(c)
        if index == 2:
            # Preserve additional large groups outside the primary aspect range.
            anchors = [c for c in local if len(c['members']) > 100
                       and c['area'] > max_area*.025]
            local = anchors + [c for c in local if c not in anchors]
        selected.extend(local[:cap])
        leftovers.extend(local[cap:])
    leftovers.sort(key=lambda c: c['native_type'] not in priority_types)
    selected.extend(leftovers[:max(0, limit-len(selected))])
    # The atlas budget may trim generated proposals, never complete large
    # native groups. Busy pages must not silently lose a vehicle/end view.
    selected_members = {tuple(c['member_ids']) for c in selected if c['native_type'] == 'AGroup'}
    for c in candidates:
        if (c['native_type'] == 'AGroup' and c['area'] > max_area*.025
                and tuple(c['member_ids']) not in selected_members
                and not any(set(c['member_ids']) <= set(old['member_ids'])
                            for old in selected if old['native_type'] == 'AGroup')):
            selected.append(c)
            selected_members.add(tuple(c['member_ids']))
    for c in selected:
        span = c.get('source_span')
        c['native_parent_spans'] = [g['source_span'] for g in candidates
            if span and g is not c and g['source_span'][0] < span[0]
            and span[1] <= g['source_span'][1]
            and set(c['member_ids']) <= set(g['member_ids'])]
    attach_native_contexts(selected, candidates)
    for i, c in enumerate(selected):
        c.update(id=f'G{i+1:03d}', score=1, fill='none')
    return selected, {'native_candidates': len(candidates), 'loose_graphic_candidates': len(loose),
                      'provided_emblem_candidates': sum(c['native_type'] in ('DoorEmblem', 'EmblemLogo') for c in selected),
                      'provided_wordmark_candidates': sum(c['native_type'] in ('WordmarkText', 'WordmarkLogo') for c in selected),
                      'provided_text_blocks': sum(c['native_type']=='TextBlock' for c in selected),
                      'paint_surface_candidates': sum(c['native_type']=='PaintSurface' for c in paint),
                      'paint_region_candidates': sum(c['native_type']=='PaintRegion' for c in paint),
                      'provided_paint_surfaces': sum(c['native_type']=='PaintSurface' for c in selected),
                      'provided_paint_regions': sum(c['native_type']=='PaintRegion' for c in selected),
                      'provided_candidates': len(selected),
                      'omitted_or_deduplicated_candidates': len(candidates)+len(badges)+len(text_blocks)+len(wordmarks)+len(loose)+len(paint)-len(selected),
                      'candidate_limit': limit, 'reference_library_used': False}


def candidate_atlas(candidates, path, *, columns=4):
    tw, th = 400, 190
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


def detail_candidates(candidates):
    """Show native decorations as clearly as generated path proposals.

    This selects visual evidence only; it does not assign category hints.
    """
    bounds = union_bounds(candidates)
    page_width = max(bounds[2]-bounds[0], 1e-6)
    result = []
    for c in candidates:
        box = c['bbox']
        w, h = box[2]-box[0], box[3]-box[1]
        native_graphic = (
            c['native_type'] in ('AGroup', 'AComposite')
            and 2 <= len(c['members']) <= 80
            and w >= page_width*.08 and w >= h
            and any(o.get('fill') not in (None, 'none') for o in c['members']))
        if (native_graphic or c.get('coverage_anchor')
                or c['native_type'] in ('DetailGraphic', 'CompactGraphic', 'TextBlock', 'DoorEmblem', 'EmblemLogo', 'SpatialPaths', 'SourcePath')):
            result.append(c)
    return result


def prepare(source, directory, limit=120):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    objects, containers = load_scene(source)
    candidates, coverage = build_candidates(objects, containers, limit)
    candidate_atlas(candidates, directory/'group_atlas.png')
    detail = detail_candidates(candidates)
    if detail:
        candidate_atlas(detail, directory/'group_details.png')
    rasterize(svg_document(objects, width=1800, background='#ffffff'), directory/'source_scene.png')
    return objects, candidates, coverage


def context_regions(candidates):
    """Geometric crop proposals only; never infer a vehicle/category label."""
    largest = max((c.get('area', 0) for c in candidates), default=0)
    proposals = [c for c in candidates if c.get('native_type') == 'AGroup'
                 and len(c.get('member_ids', [])) >= 60
                 and .4 < c.get('aspect', 0) < 5.5
                 and c.get('area', 0) > largest*.025]
    # Prefer bounded local groups over a page parent containing several views.
    local = []
    for parent in proposals:
        children = [c for c in proposals if c is not parent
                    and c['area'] < parent['area']*.8
                    and _inside_region(c['bbox'], parent['bbox'])]
        if not any(not _boxes_overlap(a['bbox'], b['bbox'])
                   for i, a in enumerate(children) for b in children[i+1:]):
            local.append(parent)
    result = []
    for c in sorted(local, key=lambda c: -c['area']):
        if any(_inside_region(c['bbox'], old['bbox']) for old in result):
            continue
        result.append(c)
    return result


def _boxes_overlap(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _inside_region(box, region):
    dx = max(region[2]-region[0], 1)*.002
    dy = max(region[3]-region[1], 1)*.002
    return (region[0]-dx <= box[0] and region[1]-dy <= box[1]
            and box[2] <= region[2]+dx and box[3] <= region[3]+dy)


def candidate_region(candidate, anchors):
    forced = candidate.get('body_anchor_id')
    if forced:
        return next((a for a in anchors if a['id'] == forced), None)
    if not candidate.get('bbox'):
        return None
    matches = [a for a in anchors if _inside_region(candidate['bbox'], a['bbox'])]
    return min(matches, key=lambda a: a.get('area', float('inf')), default=None)


def region_batches(candidates, size, anchors):
    """Keep one region per batch and every input ID exactly once, unchanged."""
    if size < 1:
        raise ValueError('区域批次数量必须大于零。')
    buckets = {a['id']: [] for a in anchors}
    buckets[None] = []
    for c in candidates:
        anchor = candidate_region(c, anchors)
        buckets[anchor['id'] if anchor else None].append(c)
    batches = []
    for key, bucket in buckets.items():
        if key is not None:
            bucket = sorted(bucket, key=lambda c: (
                c['id'] != key,
                c.get('native_type') not in ('PaintRegion', 'PaintSurface', 'SourcePath'),
                -c.get('area', 0)))
        batches.extend(bucket[start:start+size] for start in range(0, len(bucket), size))
    return batches


def render_region_context(candidates, directory, config):
    """Original crop + ID locator + current paint, using exact source bounds."""
    objects = config.get('_context_objects')
    if not objects or not candidates:
        return False
    anchors = config.get('_context_anchors', [])
    groups = {}
    for c in candidates:
        anchor = candidate_region(c, anchors)
        key = anchor['id'] if anchor else None
        groups.setdefault(key, {'anchor': anchor, 'candidates': []})['candidates'].append(c)
    # Multi-region conflict calls still get every candidate in a bounded sheet.
    rows = list(groups.values())
    if len(rows) > 6:
        rows = [{'anchor': None, 'candidates': candidates}]
    paint = set(config.get('_context_paint_ids', []))
    auditing = config.get('_paint_completeness', False)
    pw, ph = 560, 340
    cols = 3 if auditing else 2
    sheet = Image.new('RGB', (pw*cols, (ph+48)*len(rows)), '#e5e7eb')
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 14)
    draw = ImageDraw.Draw(sheet)
    metadata = []
    for row, group in enumerate(rows):
        anchor, batch = group['anchor'], group['candidates']
        bounds = union_bounds(([anchor] if anchor else [])+batch)
        dx, dy = (bounds[2]-bounds[0])*.04, (bounds[3]-bounds[1])*.04
        bounds = [bounds[0]-dx, bounds[1]-dy, bounds[2]+dx, bounds[3]+dy]
        visible = [o for o in objects if _boxes_overlap(o['bbox'], bounds)]
        def panel(items):
            svg = svg_document(items, bounds=bounds, width=pw-20, margin=0, background='#ffffff')
            vector = VectorImage(svg)
            scale = min((pw-20)/vector.width, (ph-10)/vector.height)
            size = (max(1, round(vector.width*scale)), max(1, round(vector.height*scale)))
            im, _, _ = vector.render(scale, (0, 0), size)
            return im.convert('RGB')
        original = panel(visible)
        locator = original.copy()
        marks = ImageDraw.Draw(locator)
        sx, sy = original.width/(bounds[2]-bounds[0]), original.height/(bounds[3]-bounds[1])
        labels = []
        for c in batch:
            b = c['bbox']
            box = [(b[0]-bounds[0])*sx, (bounds[3]-b[3])*sy,
                   (b[2]-bounds[0])*sx, (bounds[3]-b[1])*sy]
            marks.rectangle(box, outline='#d00000', width=2)
            lx = min(max(0, box[0]+2), max(0, original.width-48))
            ly = min(max(0, box[1]+2), max(0, original.height-18))
            positions = list(range(int(ly), max(1, original.height-17), 18))
            positions += list(range(0, int(ly), 18))
            for y_label in positions:
                label_box = [lx, y_label, lx+48, y_label+17]
                if not any(_boxes_overlap(label_box, old) for old in labels):
                    ly = y_label
                    break
            labels.append([lx, ly, lx+48, ly+17])
            marks.line([(box[0], box[1]), (lx, ly)], fill='#d00000', width=1)
            marks.rectangle(labels[-1], fill='white')
            marks.text((lx+1, ly), c['id'], font=font, fill='#c00000')
        panels = [('ORIGINAL', original), ('CANDIDATE LOCATIONS', locator)]
        if auditing:
            panels.append(('CURRENT PAINT - REVIEW FOR OMISSIONS / WRONG PARTS',
                           panel([o for o in visible if o['id'] in paint])))
        label = anchor['id'] if anchor else 'unassigned source area'
        y = row*(ph+48)
        for col, (title, im) in enumerate(panels):
            x = col*pw
            sheet.paste(im, (x+(pw-im.width)//2, y+30+(ph-im.height)//2))
            draw.text((x+8, y+5), title, font=font, fill='#172333')
            draw.text((x+8, y+ph+31), label+' | '+', '.join(c['id'] for c in batch), font=font, fill='#172333')
        metadata.append({'anchor_id': anchor['id'] if anchor else None, 'bbox': bounds,
                         'candidate_ids': [c['id'] for c in batch]})
    sheet.save(directory/'group_details.png')
    (directory/'region_context.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf8')
    if auditing:
        # Actual source-path removal disambiguates a filled shell from the
        # seams/grilles drawn above it. A bbox alone can enclose both.
        tw, th = 420, 180
        details = Image.new('RGB', (tw*3, th*len(candidates)), '#e5e7eb')
        labels = ImageDraw.Draw(details)
        for row, c in enumerate(candidates):
            b = c['bbox']; anchor = candidate_region(c, anchors)
            a = anchor['bbox'] if anchor else b
            dx = max((b[2]-b[0])*.15, (a[2]-a[0])*.03)
            dy = max((b[3]-b[1])*.25, (a[3]-a[1])*.08)
            crop = [b[0]-dx, b[1]-dy, b[2]+dx, b[3]+dy]
            neighbours = [o for o in objects if _boxes_overlap(o['bbox'], crop)]
            target = set(c['member_ids'])
            for col, (title, items) in enumerate((
                    ('ORIGINAL DETAIL', neighbours),
                    ('TARGET SOURCE ONLY', [o for o in neighbours if o['id'] in target]),
                    ('ORIGINAL WITHOUT TARGET', [o for o in neighbours if o['id'] not in target]))):
                svg = svg_document(items, bounds=crop, width=tw-16, margin=0, background='#bbbbbb')
                vector = VectorImage(svg)
                scale = min((tw-16)/vector.width, (th-30)/vector.height)
                size = (max(1, round(vector.width*scale)), max(1, round(vector.height*scale)))
                im, _, _ = vector.render(scale, (0, 0), size)
                x, y = col*tw, row*th
                details.paste(im.convert('RGB'), (x+(tw-im.width)//2, y+25+(th-30-im.height)//2))
                labels.text((x+6, y+3), c['id']+' | '+title, font=font, fill='#172333')
        details.save(directory/'surface_details.png')
    return True


def recognize_groups(candidates, directory, config, *, surface_audit=False, classification_batch=False):
    from vision import recognize, CompactLabelError, _validate_components
    directory = Path(directory)
    metadata = [{k: c[k] for k in ('id', 'bbox', 'native_type', 'source_span')} |
                {'path_count': len(c['member_ids'])} for c in candidates]
    if classification_batch or config.get('_region_workflow'):
        for item, candidate in zip(metadata, candidates):
            ids = set(candidate['member_ids'])
            item['shared_source_candidates'] = list(dict.fromkeys(
                c['id'] for c in config.get('_all_candidates', candidates)
                if c['id'] != candidate['id'] and ids.intersection(c['member_ids'])))
            anchor = candidate_region(candidate, config.get('_context_anchors', []))
            if anchor:
                item['context_anchor'] = anchor['id']
            if candidate.get('recheck_paint'):
                item['previous_category'] = '车漆'
    scene = {'objects': candidates, 'component_mode': True, 'group_metadata': metadata}
    stage = ('paint_consistency_audit' if config.get('_paint_consistency') else
             'body_surface_audit' if surface_audit else 'conflict_review' if directory.name == 'conflicts'
             else 'empty_recovery' if directory.parent.name == 'empty_recovery'
             else 'classification' if classification_batch else 'initial')
    scene.update(request_trace_dir=config.get('_trace_root', str(directory/'ai_requests')),
                 request_stage=stage, request_batch=config.get('_request_batch', directory.name))
    if classification_batch:
        scene['classification_batch'] = True
    if surface_audit:
        scene['body_surface_audit'] = True
    scene['paint_consistency'] = bool(config.get('_paint_consistency'))
    scene['native_group_stage'] = bool(config.get('_native_group_stage'))
    scene['local_member_stage'] = bool(config.get('_local_member_stage'))
    for item, candidate in zip(metadata, candidates):
        item['native_parent_spans'] = candidate.get('native_parent_spans', [])
        item['selected_parent_categories'] = [dict(group_id=parent['id'], category=name)
            for parent, name in config.get('_selected_group_context', [])
            if set(candidate['member_ids']) <= set(parent['member_ids'])]
    scene['confirmed_body_ids'] = config.get('_confirmed_body_ids', [])
    if scene['native_group_stage'] and not surface_audit and directory.name != 'conflicts':
        stage = 'native_groups'
    elif scene['local_member_stage'] and not surface_audit and directory.name != 'conflicts':
        stage = 'local_members'
    scene['request_stage'] = stage
    if (directory/'region_context.json').exists():
        scene['region_context'] = json.loads((directory/'region_context.json').read_text(encoding='utf8'))
        scene['paint_completeness'] = bool(config.get('_paint_completeness'))
    if (directory/'group_details.png').exists():
        scene['candidate_sheet_png'] = str(directory/'group_details.png')
    if (directory/'surface_details.png').exists():
        scene['surface_detail_png'] = str(directory/'surface_details.png')
    try:
        return recognize(scene, config.get('_source_image_path', directory/'source_scene.png'), directory/'group_atlas.png',
                         dict(config, max_candidates=max(len(candidates), 1)))
    except CompactLabelError:
        # Every retry is strictly smaller, down to a single source candidate.
        # Missing labels never become negatives; transport failures still stop.
        if len(candidates) < 2:
            attempt = config.get('_protocol_retry', 0)
            if attempt >= 2:
                raise
            return recognize_groups(candidates, directory, dict(config,
                _protocol_retry=attempt+1,
                _request_batch=f"{scene['request_batch']}/format_retry/{attempt+1}"),
                surface_audit=surface_audit, classification_batch=classification_batch)
        merged = {'categories': {name: [] for name in CATEGORIES}, 'source_regions': [],
                  'source_views': {}, 'reason': '', 'confidence': 1.0}
        size = (len(candidates)+1)//2
        for start in range(0, len(candidates), size):
            batch = candidates[start:start+size]
            local = directory/'protocol_repair'/f'{start//size+1:02d}'
            batch_inputs(batch, local, directory, config)
            if surface_audit and not (local/'region_context.json').exists() and (directory/'group_details.png').exists():
                import shutil
                shutil.copyfile(directory/'group_details.png', local/'group_details.png')
            checked = recognize_groups(batch, local, dict(config,
                                       _request_batch=f"{scene['request_batch']}/protocol_repair/{local.name}"),
                                       surface_audit=surface_audit, classification_batch=classification_batch)
            for name in CATEGORIES:
                merged['categories'][name].extend(checked['categories'][name])
            merged['source_views'].update(checked.get('source_views', {}))
            merged['reason'] = (merged['reason']+'；'+checked['reason'])[:5000]
            merged['confidence'] = min(merged['confidence'], checked['confidence'])
        return _validate_components(merged, {c['id'] for c in candidates})


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


@_serial_render
def batch_inputs(candidates, directory, source_directory, config):
    """Bounded, readable sheets; never resize or change source geometry."""
    import shutil
    from vision import compact_prompt
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(source_directory)/'source_scene.png', directory/'source_scene.png')
    compact = compact_prompt(config)
    candidate_atlas(candidates, directory/'group_atlas.png', columns=2 if compact else 4)
    if render_region_context(candidates, directory, config):
        return
    if not compact:
        detail = detail_candidates(candidates)
        if detail:
            candidate_atlas(detail, directory/'group_details.png')


def recover_empty_classification(candidates, decision, directory, config, *, batch_size=20, phase='empty_recovery'):
    """Retry an all-empty long atlas at readable scale, using the same provider.

    Keep original G IDs and require explicit model labels. Geometry proposals
    alone never become classified results. Each candidate is retried once.
    """
    import copy
    from vision import _validate_components
    if config.get('_image_cache') is not None:
        config = dict(config, _source_image_path=Path(directory)/'source_scene.png')
    coverage = {'empty_recovery_batches': 0, 'empty_recovery_classified': 0,
                'empty_recovery_conflict_groups': 0, 'empty_recovery_unresolved_groups': 0}
    if any(decision['categories'].values()) or not candidates:
        return decision, coverage
    merged = copy.deepcopy(decision)
    merged.update(source_regions=[], source_views={})
    reasons, confidences = [], []
    batches = (region_batches(candidates, batch_size, config.get('_context_anchors', []))
               if config.get('_region_workflow') else
               [candidates[start:start+batch_size] for start in range(0, len(candidates), batch_size)])
    def prepare_batch(number, batch):
        path = Path(directory)/phase/f'{number:02d}'
        batch_inputs(batch, path, directory, config)
        return path
    def classify_batch(batch, path):
        checked = recognize_groups(batch, path, config, classification_batch=True)
        _validate_components(checked, {c['id'] for c in batch})
        return checked
    for batch, path, checked in _ordered_batches(batches, config, prepare_batch, classify_batch):
        (path/'decision.json').write_text(json.dumps(checked, ensure_ascii=False, indent=2), encoding='utf8')
        coverage['empty_recovery_batches'] += 1
        for name in CATEGORIES:
            merged['categories'][name].extend(checked['categories'][name])
            coverage['empty_recovery_classified'] += len(checked['categories'][name])
        merged['source_views'].update(checked.get('source_views', {}))
        for region in checked.get('source_regions', []):
            number = len(merged['source_regions'])+1
            suffix = f'（复核{number}）'
            merged['source_regions'].append(dict(region, id=f'R{number}',
                                                  name=region['name'][:40-len(suffix)]+suffix))
        if not checked.get('source_regions'):
            for gid in checked['categories']['车身']:
                number = len(merged['source_regions'])+1
                view = checked.get('source_views', {}).get(gid, '待确认视图')
                merged['source_regions'].append(dict(id=f'R{number}', name=f'{view}{number}',
                                                     source_view=view, anchor_ids=[gid]))
        reasons.append(checked['reason'])
        confidences.append(checked['confidence'])
    label = {'native_groups': '原生整组识别', 'local_members': '局部成员分类'}.get(phase, '全空结果分批复核')
    merged['reason'] += '；'+label+'：'+'；'.join(reasons)
    # Confidence is not accuracy; preserve the most cautious batch assessment.
    merged['confidence'] = min(confidences)
    _validate_components(merged, {c['id'] for c in candidates})
    # Different G IDs can own the same source paths. Bring contradictory
    # labels together once; do not resolve semantics by batch order or colour.
    return review_classification_conflicts(candidates, merged, directory, config, phase, coverage)


def review_classification_conflicts(candidates, merged, directory, config, phase, coverage):
    """Resolve overlaps after either ordinary batches or sequential stages."""
    from vision import _validate_components
    conflicts = recovery_conflicts(candidates, merged)
    if conflicts:
        path = Path(directory)/phase/'conflicts'
        review = [c for c in candidates if c['id'] in conflicts]
        batch_inputs(review, path, directory, config)
        checked = recognize_groups(review, path, config, classification_batch=True)
        _validate_components(checked, conflicts)
        (path/'decision.json').write_text(json.dumps(checked, ensure_ascii=False, indent=2), encoding='utf8')
        for name in CATEGORIES:
            if name == '车身':
                continue
            merged['categories'][name] = [g for g in merged['categories'][name] if g not in conflicts]
            merged['categories'][name].extend(checked['categories'][name])
        # If the focused call still disagrees, retain these groups only in
        # the manual pool. Never pick a winning category automatically.
        unresolved = recovery_conflicts(candidates, merged)
        for name in CATEGORIES:
            if name != '车身':
                merged['categories'][name] = [g for g in merged['categories'][name] if g not in unresolved]
        coverage['empty_recovery_conflict_groups'] = len(conflicts)
        retained = {gid for ids in merged['categories'].values() for gid in ids}
        coverage['empty_recovery_unresolved_groups'] = len(conflicts-retained)
        merged['reason'] += '；跨候选源对象冲突复核：'+checked['reason']
        merged['confidence'] = min(merged['confidence'], checked['confidence'])
    _validate_components(merged, {c['id'] for c in candidates})
    coverage['empty_recovery_classified'] = sum(len(ids) for ids in merged['categories'].values())
    return merged, coverage


def classify_native_first(candidates, directory, config):
    """Classify intact source groups before exposing generated local members.

    Each stage is fully validated before its decision becomes later context.
    Existing IDs and member arrays are immutable throughout both stages.
    """
    from vision import component_batch_size, _validate_components
    size = component_batch_size(config) or 20
    native = [c for c in candidates if c['native_type'] in ('AGroup', 'AComposite')]
    local = [c for c in candidates if c['native_type'] not in ('AGroup', 'AComposite')]
    merged = {'categories': {name: [] for name in CATEGORIES}, 'source_regions': [],
              'source_views': {}, 'reason': '', 'confidence': 1.0}
    coverage = {'native_group_stage_candidates': len(native), 'local_member_stage_candidates': len(local),
                'initial_classification_batches': 0}
    for phase, pool in (('native_groups', native), ('local_members', local)):
        if not pool:
            continue
        anchors = selected_body_anchors(candidates, merged)
        context = dict(config, _native_group_stage=phase == 'native_groups',
                       _local_member_stage=phase == 'local_members',
                       _confirmed_body_ids=list(merged['categories']['车身']),
                       _selected_group_context=[(c, name) for name, ids in merged['categories'].items()
                                                for c in candidates if c['id'] in ids])
        if anchors:
            context['_context_anchors'] = anchors
        blank = {'categories': {name: [] for name in CATEGORIES}, 'reason': '', 'confidence': 1.0}
        checked, stats = recover_empty_classification(pool, blank, directory, context,
                                                      batch_size=size, phase=phase)
        coverage[phase+'_batches'] = stats['empty_recovery_batches']
        coverage['initial_classification_batches'] += stats['empty_recovery_batches']
        for name in CATEGORIES:
            merged['categories'][name].extend(checked['categories'][name])
        merged['source_views'].update(checked.get('source_views', {}))
        merged['reason'] = (merged['reason']+'；'+checked['reason'])[-5000:]
        merged['confidence'] = min(merged['confidence'], checked['confidence'])
        coverage[phase+'_unresolved_groups'] = stats['empty_recovery_unresolved_groups']
    _validate_components(merged, {c['id'] for c in candidates})
    merged, coverage = review_classification_conflicts(candidates, merged, directory,
        dict(config, _native_group_stage=False, _local_member_stage=False),
        'stage_conflicts', coverage)
    # A local member labelled structural must not create an overlapping region
    # inside its already selected complete vehicle parent.
    for number, anchor in enumerate(selected_body_anchors(candidates, merged), 1):
        view = merged['source_views'].get(anchor['id'], '待确认视图')
        merged['source_regions'].append(dict(id=f'R{number}', name=f'{view}{number}',
                                            source_view=view, anchor_ids=[anchor['id']]))
    _validate_components(merged, {c['id'] for c in candidates})
    return merged, coverage


def body_group_audit(objects, candidates, decision):
    """Record full parent membership and explicit category transfers."""
    resolved = resolve_categories(objects, candidates, decision)
    retained_ids = set(resolved['车身'])
    result = []
    for c in candidates:
        if c['id'] not in decision['categories']['车身']:
            continue
        members = set(c['member_ids'])
        retained = [oid for oid in c['member_ids'] if oid in retained_ids]
        transfers = {name: [oid for oid in ids if oid in members]
                     for name, ids in resolved.items() if name != '车身' and members.intersection(ids)}
        result.append(dict(group_id=c['id'], native_type=c['native_type'],
            source_span=c.get('source_span'), native_parent_spans=c.get('native_parent_spans', []),
            original_member_ids=list(c['member_ids']), retained_member_ids=retained,
            transfers=transfers))
    return result


def recovery_conflicts(candidates, decision):
    """Return whole groups with contradictory nonbody source ownership."""
    members = {c['id']: set(c['member_ids']) for c in candidates}
    owners = [(name, gid) for name, ids in decision['categories'].items()
              if name != '车身' for gid in ids]
    conflicts = set()
    for index, (name, gid) in enumerate(owners):
        for other_name, other_gid in owners[index+1:]:
            if (name != other_name and {name, other_name} != {'logo', '门徽'}
                    and members[gid].intersection(members[other_gid])):
                conflicts.update((gid, other_gid))
    return conflicts


def selected_body_anchors(candidates, decision):
    """Use the selected parent as context for its mislabelled local members.

    This refines review context only, never the model's category ownership.
    A roof fill selected as body must not become its own vehicle crop.
    """
    selected = set(decision['categories']['车身'])
    bodies = [c for c in candidates if c['id'] in selected]
    memberships = {c['id']: set(c['member_ids']) for c in bodies}
    return [c for c in bodies if not any(
        memberships[c['id']] < memberships[parent['id']]
        and _inside_region(c['bbox'], parent['bbox']) for parent in bodies)]


def residual_surface_candidates(objects, candidates, decision, limit=None):
    """Expose residual and omitted filled paths at each body's local scale.

    This is a review queue, not a paint detector. Windows, lamps and tyres
    remain in it for visual rejection; no colour rule removes them from body.
    Omitted sibling decorations precede residual structure within each region.
    """
    from core.paint import style_capability, color_is_neutral
    resolved = resolve_categories(objects, candidates, decision)
    body_ids = set(resolved['车身'])
    reserved = {i for name, ids in resolved.items() if name != '车身' for i in ids}
    bodies = selected_body_anchors(candidates, decision)
    text_ids = {i for c in candidates if c['native_type'] in ('AText', 'AArcText', 'TextBlock')
                for i in c['member_ids']}
    queues = [[] for _ in bodies]
    by_id = {o['id']: o for o in objects}
    stripe_ids = set(resolved['彩条'])
    protected = reserved-stripe_ids
    grouped_ids, seen_groups = set(), set()
    known_colors = set()
    for name in ('彩条', '车漆'):
        for oid in resolved.get(name, []):
            obj = by_id.get(oid)
            if not obj:
                continue
            fill = obj.get('fill')
            if fill and not color_is_neutral(fill):
                known_colors.add(fill.lower())
            gradient = obj.get('gradient')
            if gradient:
                for stop in gradient.get('definition', {}).get('stops', []):
                    color = stop.get('color')
                    if color and not color_is_neutral(color):
                        known_colors.add(color.lower())

    def color_tier(candidate):
        members = candidate.get('members') or [
            by_id[mid] for mid in candidate.get('member_ids', []) if mid in by_id
        ]
        has_gradient = any(m.get('gradient') for m in members)
        fills = [m['fill'].lower() for m in members
                 if m.get('fill') and m['fill'] != 'none']
        if has_gradient or any(f in known_colors for f in fills):
            return 0
        if any(not color_is_neutral(f) for f in fills):
            return 1
        return 2
    for seed in candidates:
        context = seed.get('native_context')
        if not context or not set(seed['member_ids']) <= stripe_ids:
            continue
        ids = set(context['member_ids'])
        if ids & (protected | text_ids) or frozenset(ids) in seen_groups:
            continue
        # Identify the vehicle from the selected seed, not the wider group's
        # bounds: full native stripes can deliberately extend past the shell.
        b = seed['bbox']
        owners = [(i, c) for i, c in enumerate(bodies)
                  if c['bbox'][0] <= b[0] and c['bbox'][1] <= b[1]
                  and b[2] <= c['bbox'][2] and b[3] <= c['bbox'][3]]
        if len(owners) != 1:
            continue
        index, owner = owners[0]
        a, b = owner['bbox'], context['bbox']
        overlap = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(
            0, min(a[3], b[3])-max(a[1], b[1]))
        if overlap/max(context['area'], 1e-6) < .8:
            # A native group spanning two views is not a single decoration.
            continue
        queues[index].append(dict(context, members=[by_id[i] for i in context['member_ids']],
                                 native_type='NativeDecorationGroup', body_anchor_id=owner['id'],
                                 omitted_decoration=True))
        grouped_ids.update(ids);seen_groups.add(frozenset(ids))
    for obj in objects:
        if (obj['id'] in reserved or obj['id'] in text_ids or obj['id'] in grouped_ids
                or obj.get('details', {}).get('closed') is False
                or style_capability(obj) == 'unsupported'):
            continue
        b = obj['bbox'];w, h = b[2]-b[0], b[3]-b[1]
        owners = [(i, c) for i, c in enumerate(bodies) if obj['id'] in c['member_ids']]
        # Decorations are frequently siblings of the structural body group.
        # Review omitted source objects inside the same vehicle, too; selecting
        # only body descendants silently drops complete motifs and other colours.
        if not owners and obj['id'] not in body_ids:
            owners = [(i, c) for i, c in enumerate(bodies)
                      if b[0] >= c['bbox'][0] and b[1] >= c['bbox'][1]
                      and b[2] <= c['bbox'][2] and b[3] <= c['bbox'][3]]
        if not owners or min(w, h) <= 1e-5:
            continue
        index, owner = min(owners, key=lambda pair: pair[1]['area'])
        if w*h < owner['area']*.001:
            continue
        queues[index].append(dict(members=[obj], member_ids=[obj['id']], bbox=b,
                                 native_type='PaintSurface', source_span=[obj['source_offset']]*2,
                                 area=w*h, aspect=w/h, body_anchor_id=owner['id'],
                                 omitted_decoration=obj['id'] not in body_ids))
    for queue in queues:
        queue.sort(key=lambda c: (c['native_type'] != 'NativeDecorationGroup',
                                  not c['omitted_decoration'],
                                  color_tier(c),
                                  -c['area']))
    # Round robin prevents a detailed side view from displacing end-view paint.
    ordered = [queue[i] for i in range(max(map(len, queues), default=0))
               for queue in queues if i < len(queue)]
    first = max((int(c['id'][1:]) for c in candidates), default=0)+1
    provided = ordered if limit is None else ordered[:limit]
    for index, c in enumerate(provided, first):
        c['id'] = f'G{index:03d}'
    return provided, len(ordered)


def refine_body_surfaces(objects, candidates, decision, directory, config):
    """Visually audit residual body surfaces and merge only explicit labels."""
    import copy
    regional = config.get('_region_workflow', False)
    selected_paint = set(decision['categories']['车漆'])
    recheck = [dict(c, recheck_paint=True) for c in candidates if c['id'] in selected_paint] if regional else []
    # The initial atlas budget is not a completeness budget. Review every
    # eligible residual surface in bounded regional batches, including those
    # ranked after the first 120 (small roof shells frequently land there).
    audit, total = residual_surface_candidates(objects, candidates, decision)
    coverage = {'body_surface_audit_candidates': len(audit),
                'body_surface_audit_eligible': total,
                'body_surface_audit_omitted': total-len(audit),
                'body_surface_audit_classified': 0}
    coverage['paint_completeness_rechecked'] = len(recheck)
    audit = recheck+audit
    if not audit:
        return decision, coverage
    if regional:
        anchors = selected_body_anchors(candidates, decision)
        config = dict(config, _context_objects=objects, _context_anchors=anchors,
                      _context_paint_ids=resolve_categories(objects, candidates, decision)['车漆'],
                      _paint_completeness=True, _all_candidates=candidates+audit)
    path = Path(directory)/'body_surface_audit';path.mkdir(parents=True, exist_ok=True)
    candidate_atlas(audit, path/'group_atlas.png')
    rasterize(svg_document(objects, width=2000, background='#ffffff'), path/'source_scene.png')
    body_ids = set(resolve_categories(objects, candidates, decision)['车身'])
    rasterize(svg_document([o for o in objects if o['id'] in body_ids], width=2000,
                           background='#bbbbbb'), path/'group_details.png')
    from vision import component_batch_size, compact_prompt
    if config.get('_image_cache') is not None:
        config = dict(config, _source_image_path=path/'source_scene.png')
    size = component_batch_size(config) or 20
    if regional or (size and len(audit) > size):
        checked = {'categories': {name: [] for name in CATEGORIES}, 'source_regions': [],
                   'source_views': {}, 'reason': '', 'confidence': 1.0}
        batches = (region_batches(audit, size, config['_context_anchors']) if regional else
                   [audit[start:start+size] for start in range(0, len(audit), size)])
        coverage['paint_completeness_batches'] = len(batches) if regional else 0
        @_serial_render
        def prepare_batch(number, batch):
            local = path/'batches'/f'{number:02d}'
            batch_inputs(batch, local, path, config)
            if compact_prompt(config) and not regional:
                anchors = {c['body_anchor_id'] for c in batch}
                context = [c for c in candidates if c['id'] in anchors]
                if context:
                    candidate_atlas(context, local/'group_details.png', columns=2)
            return local
        def classify_batch(batch, local):
            response = recognize_groups(batch, local, config, surface_audit=True)
            from vision import _validate_components
            _validate_components(response, {c['id'] for c in batch})
            if any(response['categories'][name] for name in ('车身', '文字', 'logo', '门徽')):
                raise ValueError('残留面复核只能选择车漆与彩条。')
            return response
        for batch, local, response in _ordered_batches(batches, config, prepare_batch, classify_batch):
            (local/'decision.json').write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding='utf8')
            for name in ('车漆', '彩条'):
                checked['categories'][name].extend(response['categories'][name])
            checked['reason'] += response['reason']+'；'
            checked['confidence'] = min(checked['confidence'], response['confidence'])
    else:
        checked = recognize_groups(audit, path, config, surface_audit=True)
    (path/'decision.json').write_text(json.dumps(checked, ensure_ascii=False, indent=2), encoding='utf8')
    merged = copy.deepcopy(decision)
    if regional:
        # Reconsider only paint IDs explicitly included in the complete review.
        # A successful omission rejects the old label, not the source object.
        merged['categories']['车漆'] = [g for g in merged['categories']['车漆']
                                      if g not in {c['id'] for c in recheck}]
    for name in ('车漆', '彩条'):
        merged['categories'][name].extend(checked['categories'][name])
        coverage['body_surface_audit_classified'] += sum(g not in selected_paint for g in checked['categories'][name])
    if regional:
        coverage['paint_completeness_removed'] = len(selected_paint-set(merged['categories']['车漆']))
    merged.setdefault('source_views', {}).update(checked.get('source_views', {}))
    merged['reason'] += '；车身残留面复核：'+checked.get('reason', '')
    # Commit proposals only after the complete merged protocol validates.
    new = [c for c in audit if c['id'] not in {old['id'] for old in candidates}]
    resolve_categories(objects, candidates+new, merged)
    candidates.extend(new)
    return merged, coverage


def refine_paint_consistency(objects, candidates, decision, directory, config):
    """Recheck unclaimed same-appearance sources, never infer their category.

    Cross-view matches are only a reason for one focused model review. Neutral
    structure is excluded from this extra pass; all ordinary audit remains.
    """
    import copy
    from core.paint import color_is_neutral, style_capability
    resolved = resolve_categories(objects, candidates, decision)
    by_id = {o['id']: o for o in objects}
    def signature(obj):
        if style_capability(obj) == 'unsupported':
            return None
        gradient = obj.get('gradient')
        if gradient:
            definition = gradient.get('definition', {})
            stops = [(s['offset'], s['color'].lower()) for s in definition.get('stops', [])]
            if any(not color_is_neutral(color) for _, color in stops):
                return ('gradient', definition.get('kind'), tuple(stops))
        fill = obj.get('fill')
        if fill and not gradient and not color_is_neutral(fill):
            return ('solid', fill.lower())
        return None
    styles = {key for oid in resolved['车漆'] if (key := signature(by_id[oid])) is not None}
    claimed = {oid for name, ids in resolved.items() if name != '车身' for oid in ids}
    anchors = selected_body_anchors(candidates, decision)
    pool, seen = [], set()
    first = max((int(c['id'][1:]) for c in candidates), default=0)+1
    for c in candidates:
        if len(c['member_ids']) != 1 or c['native_type'] not in ('PaintSurface', 'SourcePath'):
            continue
        oid = c['member_ids'][0]
        if oid in claimed or oid in seen or signature(by_id[oid]) not in styles:
            continue
        anchor = candidate_region(c, anchors)
        if anchor is None:
            continue
        seen.add(oid)
        pool.append(dict(c, id=f'G{first+len(pool):03d}', native_type='PaintSurface',
                         body_anchor_id=anchor['id']))
    coverage = {'paint_consistency_candidates': len(pool), 'paint_consistency_added': 0}
    if not pool:
        return decision, coverage
    context = dict(config, _region_workflow=True, _context_objects=objects,
                   _context_anchors=anchors, _context_paint_ids=resolved['车漆'],
                   _paint_completeness=True, _paint_consistency=True, _all_candidates=candidates+pool)
    merged = copy.deepcopy(decision)
    for number, c in enumerate(pool, 1):
        local = Path(directory)/'paint_consistency_audit'/f'{number:02d}'
        batch_inputs([c], local, directory, context)
        response = recognize_groups([c], local, context, surface_audit=True)
        from vision import _validate_components
        _validate_components(response, {c['id']})
        if any(response['categories'][name] for name in ('车身', '文字', 'logo', '门徽')):
            raise ValueError('一致性复核只能选择车漆与彩条。')
        (local/'decision.json').write_text(json.dumps(response, ensure_ascii=False, indent=2), encoding='utf8')
        for name in ('车漆', '彩条'):
            merged['categories'][name].extend(response['categories'][name])
        merged['reason'] = (merged.get('reason', '')+'；同填充对象复核：'+response['reason'])[-5000:]
    _validate_components(merged, {c['id'] for c in candidates+pool})
    resolved_after = resolve_categories(objects, candidates+pool, merged)
    coverage['paint_consistency_added'] = len(set(resolved_after['车漆'])-set(resolved['车漆']))
    candidates.extend(pool)
    return merged, coverage


def render_stripe_views(objects, ids, regions, directory, digest):
    """Write one source-coordinate SVG/PNG pair per stripe region."""
    from core.source_regions import normalize_source_regions, object_regions, split_by_region, region_for_ids
    from core.svg_layers import layered_svg
    regions = normalize_source_regions(regions)
    assignments = object_regions(objects, regions, partial_ids=ids)
    records = []
    for number, group in enumerate(split_by_region(ids, assignments, objects=objects), 1):
        region = region_for_ids(group, assignments, regions)
        name = region['name'] if region else '待确认区域'
        view = region['source_view'] if region else '待确认视图'
        selected = set(group)
        metadata = dict(source_sha256=digest, selected_ids=group, human_reviewed=False,
                        source_view=view, source_region=region['id'] if region else None)
        svg, _ = layered_svg([o for o in objects if o['id'] in selected], {'彩条': group},
                             width=2000, layer_names=('彩条',), metadata=metadata)
        folder = Path(directory)/'stripe_views'
        folder.mkdir(parents=True, exist_ok=True)
        # Numeric filenames keep untrusted region names out of filesystem paths.
        svg_path, png_path = folder/f'{number:02d}.svg', folder/f'{number:02d}.png'
        svg_path.write_text(svg, encoding='utf8')
        rasterize(svg, png_path, max_width=2000)
        records.append(dict(metadata, region_name=name, svg=str(svg_path.resolve()), png=str(png_path.resolve())))
    return records


def run(source, directory, config, *, use_ai=True):
    source, directory = Path(source), Path(directory)
    from vision import ImageInputCache
    config = dict(config, _trace_root=str(directory/'ai_requests'), _image_cache=ImageInputCache())
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    objects, candidates, coverage = prepare(source, directory, min(config['max_candidates'], 120))
    config.update(_region_workflow=True, _context_objects=objects,
                  _context_anchors=context_regions(candidates), _all_candidates=candidates)
    coverage['context_region_proposals'] = len(config['_context_anchors'])
    from vision import request_summary
    decision = {'categories': {name: [] for name in CATEGORIES},
                'reason': '手工复核模式，尚未分类；未调用 AI。', 'confidence': 0.0}
    if use_ai:
        decision, initial_coverage = classify_native_first(candidates, directory, config)
        coverage.update(initial_coverage)
        coverage['empty_recovery_batches'] = 0
    if use_ai:
        decision, audit_coverage = refine_body_surfaces(objects, candidates, decision, directory, config)
        coverage.update(audit_coverage)
        decision, consistency_coverage = refine_paint_consistency(objects, candidates, decision, directory, config)
        coverage.update(consistency_coverage)
    categories = resolve_categories(objects, candidates, decision)
    report = {'source': str(source.resolve()), 'source_sha256': digest, 'coverage': coverage,
              'decision': decision, 'categories': categories, 'status': 'review', 'views': []}
    report['body_group_audit'] = body_group_audit(objects, candidates, decision)
    report['ai_request_summary'] = request_summary(directory/'ai_requests')
    report['ai_requests_json'] = str((directory/'ai_requests.json').resolve())
    (directory/'ai_requests.json').write_text(json.dumps(report['ai_request_summary'], ensure_ascii=False, indent=2), encoding='utf8')
    report['candidates'] = review_candidates(candidates, decision)
    from core.source_regions import build_source_regions
    report['source_regions'] = build_source_regions(candidates, decision)
    report['stripe_patterns'] = render_stripe_views(objects, categories['彩条'], report['source_regions'], directory, digest)
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
    result = []
    for c in candidates:
        recommended = [name for name, groups in decision['categories'].items() if c['id'] in groups]
        hints = list(recommended)
        if not hints:
            # Geometry/appearance proposals are not semantic classifications.
            # Keep unclassified surfaces, graphics and logos in the manual
            # pool instead of presenting vehicle parts as category results.
            if c['native_type'] in ('AArcText', 'DoorEmblem'):
                hints = ['门徽']
            elif c['native_type'] in ('WordmarkText', 'TextBlock'):
                hints = ['文字']
            elif c['native_type'] == 'AText':
                hints = ['文字']
            elif c['area'] > largest*.025 and 2.2 < c['aspect'] < 5.5 and len(c['member_ids']) > 100:
                hints = ['车身']
        result.append({'id':c['id'], 'label':c['id']+' · '+c['native_type'],
                       'native_type': c['native_type'],
                       'source_span': c.get('source_span'),
                       'native_parent_spans': c.get('native_parent_spans', []),
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
