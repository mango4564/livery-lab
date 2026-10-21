"""Bounded, transactional visual inspection of six-category source selections.

Only decoded source memberships may change. General repairs require a complete
region recheck. Body-to-paint moves may commit after a focused visual recheck;
later failures roll back only pending edits. Approval never implies full FS support.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

import olefile
from PIL import Image, ImageDraw, ImageFont

from geometry import svg_document, union_bounds
from core.fs_container import decode_preview
from core.paint import normalize_categories, style_capability, color_is_neutral
from core.source_regions import build_source_regions, object_regions
from core.svg_layers import layered_svg
from vector_preview import VectorImage

KINDS = {'missing', 'extra', 'wrong_category', 'incomplete', 'uncertain', 'source_mismatch'}
DEFAULTS = {'audit_enabled': True, 'audit_page_size': 12,
            'audit_max_requests': 16, 'audit_repair_rounds': 2,
            'audit_timeout_seconds': 240}


def audit_options(config):
    options = {name: config.get(name, value) for name, value in DEFAULTS.items()}
    if not isinstance(options['audit_enabled'], bool):
        raise ValueError('audit_enabled 必须为布尔值。')
    for name, low, high in (('audit_page_size', 4, 24), ('audit_max_requests', 1, 64),
                            ('audit_repair_rounds', 0, 3), ('audit_timeout_seconds', 1, 600)):
        value = options[name]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f'{name} 必须为 {low} 至 {high} 的整数。')
    return options


def validate_audit(payload, allowed):
    """Reject invented handles, contradictory verdicts, and unbounded patches."""
    from components import CATEGORIES
    from vision import VisionError
    def reject():
        raise VisionError('六类门禁响应格式、对象范围或检验结论无效，已保留修复前选择。')
    if (not isinstance(payload, dict)
            or set(payload) != {'verdict', 'issues', 'patches', 'source_check'}
            or not isinstance(payload['verdict'], str)
            or payload['verdict'] not in ('pass', 'repair', 'review')
            or not isinstance(payload['source_check'], str)
            or payload['source_check'] not in ('not_assessed', 'consistent', 'uncertain', 'mismatch')):
        reject()
    issues, patches = payload['issues'], payload['patches']
    if not isinstance(issues, list) or len(issues) > 48 or not isinstance(patches, list) or len(patches) > 6:
        reject()
    def ids(value):
        if (not isinstance(value, list) or len(value) > len(allowed)
                or any(not isinstance(i, str) or i not in allowed for i in value)
                or len(set(value)) != len(value)):
            reject()
    for issue in issues:
        if (not isinstance(issue, dict) or set(issue) != {'category', 'kind', 'candidate_ids', 'evidence'}
                or not isinstance(issue['category'], str) or issue['category'] not in (*CATEGORIES, '原稿')
                or not isinstance(issue['kind'], str) or issue['kind'] not in KINDS
                or not isinstance(issue['evidence'], str) or not 1 <= len(issue['evidence']) <= 800):
            reject()
        ids(issue['candidate_ids'])
    categories = set()
    for patch in patches:
        if (not isinstance(patch, dict) or set(patch) != {'category', 'add_ids', 'remove_ids'}
                or not isinstance(patch['category'], str)
                or patch['category'] not in CATEGORIES or patch['category'] in categories):
            reject()
        categories.add(patch['category'])
        ids(patch['add_ids']); ids(patch['remove_ids'])
        if (not patch['add_ids'] and not patch['remove_ids']
                or set(patch['add_ids']) & set(patch['remove_ids'])
                or not any(i['category'] == patch['category'] for i in issues)):
            reject()
    if payload['verdict'] == 'pass' and (issues or patches):
        reject()
    if payload['verdict'] == 'repair' and (not issues or not patches):
        reject()
    if payload['verdict'] == 'review' and (not issues or patches):
        reject()
    if payload['source_check'] == 'mismatch' and payload['verdict'] != 'review':
        reject()
    return payload


def _raw_categories(candidates, decision):
    by_id = {c['id']: c for c in candidates}
    return {name: {oid for gid in groups for oid in by_id[gid]['member_ids']}
            for name, groups in decision['categories'].items()}


def _selection_candidate(gid, members, kind='AuditSelection'):
    box = union_bounds(members)
    w, h = max(box[2]-box[0], 1e-6), max(box[3]-box[1], 1e-6)
    offsets = [o.get('source_offset', 0) for o in members]
    return dict(id=gid, native_type=kind, members=members,
                member_ids=[o['id'] for o in members], bbox=box, area=w*h, aspect=w/h,
                source_span=[min(offsets, default=0), max(offsets, default=0)], score=1, fill='none')


def apply_patches(objects, candidates, decision, patches, allowed_ids, scope_ids):
    """Apply all class moves together, then validate the full source ownership."""
    from components import resolve_categories
    raw = _raw_categories(candidates, decision)
    by_id = {c['id']: c for c in candidates}
    changed = set()
    for patch in patches:
        name = patch['category']
        add, remove = set(), set()
        for key, target in (('add_ids', add), ('remove_ids', remove)):
            for gid in patch[key]:
                if gid not in allowed_ids or not set(by_id[gid]['member_ids']) <= scope_ids:
                    raise ValueError('修复越过本次检验的区域或候选范围。')
                target.update(by_id[gid]['member_ids'])
        if add & remove or not remove <= raw[name]:
            raise ValueError('修复增删冲突，或移除了该类别未选中的对象。')
        if name == '车漆' and any(o['id'] in add and (o.get('fill') in (None, 'none')
                                or style_capability(o) == 'unsupported') for o in objects):
            raise ValueError('车漆新增候选混入无填色结构线或不支持的漆面。')
        updated = (raw[name]-remove) | add
        if updated != raw[name]:
            raw[name] = updated
            changed.add(name)
    if not changed:
        raise ValueError('修复未改变任何源对象选择。')
    # Validate before building any synthetic candidate. No geometry is created.
    normalize_categories(objects, {n: [o['id'] for o in objects if o['id'] in ids]
                                   for n, ids in raw.items()})
    staged, additions = deepcopy(decision), []
    for name in sorted(changed):
        # Keep independent motifs/placements independent. A single synthetic
        # category-wide group would merge unrelated logos in the review UI.
        groups = []
        handles = list(decision['categories'][name])
        handles += [gid for p in patches if p['category'] == name for gid in p['add_ids']]
        for handle in handles:
            ids = set(by_id[handle]['member_ids']) & raw[name]
            if not ids:
                continue
            touching = [i for i, group in enumerate(groups) if group & ids]
            for index in reversed(touching):
                ids.update(groups.pop(index))
            groups.append(ids)
        staged['categories'][name] = []
        for ids in groups:
            members = [o for o in objects if o['id'] in ids]
            digest = hashlib.sha256((name+'\0'+'\0'.join(o['id'] for o in members)).encode()).hexdigest()[:20]
            gid = 'Q'+digest
            if gid not in by_id:
                additions.append(_selection_candidate(gid, members))
            staged['categories'][name].append(gid)
    resolve_categories(objects, candidates+additions, staged)
    return staged, additions


def _signature(objects, candidates, decision):
    from components import resolve_categories
    resolved = resolve_categories(objects, candidates, decision)
    return tuple((name, tuple(ids)) for name, ids in sorted(resolved.items()))


def body_paint_transfer(objects, candidates, decision, patches, page_ids, scope_ids):
    """Isolate an explicit body -> paint move, retaining source outline paths.

    A mixed native shell group often contains a fill plus duplicate wireframes.
    Only the filled source members are proposed for transfer; a focused visual
    recheck must approve their actual rendering before they can be retained.
    """
    from components import resolve_categories
    by_category = {p['category']: p for p in patches}
    body, paint = by_category.get('车身'), by_category.get('车漆')
    if not body or not paint or body['add_ids'] or paint['remove_ids']:
        return None
    by_id = {c['id']: c for c in candidates}
    def expand(ids):
        if not set(ids) <= page_ids:
            raise ValueError('漆面迁移引用了本页之外的候选。')
        return {oid for gid in ids for oid in by_id[gid]['member_ids']}
    removed, added = expand(body['remove_ids']), expand(paint['add_ids'])
    if not removed or removed != added or not removed <= scope_ids:
        return None
    current = set(resolve_categories(objects, candidates, decision)['车身'])
    if any(o['id'] in removed & current and o.get('fill') not in (None, 'none')
           and style_capability(o) == 'unsupported' for o in objects):
        return None
    members = [o for o in objects if o['id'] in removed & current
               and o.get('fill') not in (None, 'none') and style_capability(o) != 'unsupported']
    if not members:
        return None
    digest = hashlib.sha256(('body-paint\0'+'\0'.join(o['id'] for o in members)).encode()).hexdigest()[:20]
    proposal = _selection_candidate('P'+digest, members, 'PaintTransfer')
    move = [dict(category='车身', add_ids=[], remove_ids=[proposal['id']]),
            dict(category='车漆', add_ids=[proposal['id']], remove_ids=[])]
    return proposal, move


def audit_regions(objects, candidates, decision):
    """Keep source placements separate; ambiguous objects form an explicit scope."""
    regions = build_source_regions(candidates, decision)
    raw = _raw_categories(candidates, decision)
    from components import resolve_categories
    residual_body = set(resolve_categories(objects, candidates, decision)['车身'])
    graphics = set().union(*(raw[n] for n in ('彩条', '文字', 'logo', '门徽')))
    assignments = object_regions(objects, regions, partial_ids=graphics, paint_ids=raw['车漆'])
    scopes = [dict(id=r['id'], name=r['name'], bbox=r['bbox'],
                   source_view=r['source_view'],
                   objects=[o for o in objects if assignments.get(o['id']) == r['id']]) for r in regions]
    outside = [o for o in objects if o['id'] not in assignments]
    if outside:
        scopes.append(dict(id='unassigned', name='未归属区域（逐页核对）',
                           source_view='待确认视图', bbox=union_bounds(outside), objects=outside))
    for scope in scopes:
        scope['body_ids'] = residual_body & {o['id'] for o in scope['objects']}
    return [r for r in scopes if r['objects']]


def audit_pool(objects, candidates, scope):
    """Retain omitted groups and expose leaves hidden by large/mixed parents."""
    local = {o['id'] for o in scope['objects']}
    pool, memberships = [], set()
    covered = set()
    for c in candidates:
        ids = frozenset(c['member_ids'])
        if not ids or not ids <= local or ids in memberships or c['native_type'] == 'AuditSelection':
            continue
        memberships.add(ids)
        pool.append(c)
        # Native groups are inspected hierarchically as units, including large
        # structural assemblies. Do not turn 50,000 window/letter contours into
        # 50,000 thumbnails. Existing subgroups and generated alternatives stay
        # visible; leaves without a native proposal are exposed separately.
        if c['native_type'] in ('AGroup', 'AComposite', 'AText', 'AArcText',
                                'DoorEmblem', 'TextBlock', 'WordmarkText', 'WordmarkLogo'):
            covered.update(ids)
    for obj in scope['objects']:
        if obj['id'] not in covered:
            gid = 'S'+hashlib.sha256(obj['id'].encode()).hexdigest()[:20]
            if frozenset([obj['id']]) not in memberships:
                pool.append(_selection_candidate(gid, [obj], 'SourcePath'))
    def priority(c):
        # Prioritise likely information loss, never auto-assign a category.
        graphic = len(c['member_ids']) <= 100 and any(
            o.get('gradient') or (o.get('fill') and not color_is_neutral(o['fill']))
            for o in c['members'])
        detail = c['native_type'] in ('DoorEmblem', 'EmblemLogo', 'WordmarkText', 'WordmarkLogo',
                                      'DetailGraphic', 'CompactGraphic', 'AText', 'AArcText')
        whole_graphic = (graphic and c['native_type'] in ('AGroup', 'AComposite')
                         and 2 <= len(c['member_ids']) <= 100 and c.get('aspect', 0) >= 1.8)
        box = scope['bbox']
        surface = (graphic and len(c['member_ids']) == 1
                   and c['member_ids'][0] in scope.get('body_ids', ())
                   and c['area'] >= (box[2]-box[0])*(box[3]-box[1])*.001
                   and c['members'][0].get('fill') not in (None, 'none')
                   and style_capability(c['members'][0]) != 'unsupported')
        return (not surface, not whole_graphic, not detail, not graphic,
                -c['area'], c['bbox'][0], -c['bbox'][3], c['id'])
    pool.sort(key=priority)
    return pool


def _render(svg, size):
    # Fit before raster allocation, including extremely tall/narrow source views.
    vector = VectorImage(svg)
    scale = min(size[0]/max(vector.width, 1), size[1]/max(vector.height, 1))
    rendered = vector.render(scale, (0, 0), size)[0]
    result = Image.new('RGBA', size, '#bbbbbb')
    result.alpha_composite(rendered)
    return result.convert('RGB')


def render_evidence(objects, candidates, decision, scope, page, directory):
    """Render the same layered source selections used by component exports."""
    from components import CATEGORIES, resolve_categories
    directory.mkdir(parents=True, exist_ok=True)
    # Unassigned placements are cropped around this page rather than the sheet.
    bounds = union_bounds(page) if scope['id'] == 'unassigned' else scope['bbox']
    local = [o for o in objects if (o['bbox'][0] <= bounds[2] and o['bbox'][2] >= bounds[0]
                                  and o['bbox'][1] <= bounds[3] and o['bbox'][3] >= bounds[1])]
    source = directory/'source.png'
    _render(svg_document(local, bounds=bounds, width=1600), (1600, 900)).save(source)
    selected = resolve_categories(objects, candidates, decision)
    sheet = Image.new('RGB', (1600, 3*340), '#e5e7eb')
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 18)
    for index, name in enumerate(CATEGORIES):
        category_ids = set(selected[name])
        members = [o for o in local if o['id'] in category_ids]
        svg, _ = layered_svg(members, {name: [o['id'] for o in members]}, bounds=bounds,
                             width=800, layer_names=(name,))
        x, y = index % 2*800, index // 2*340
        sheet.paste(_render(svg, (790, 300)), (x, y))
        draw.text((x+8, y+305), name+f' · {len(members)} 个源对象', font=font, fill='#111111')
    sheet.save(directory/'selections.png')
    # An original-position residual image makes unselected decoration visible.
    claimed = set().union(*map(set, selected.values()))
    _render(svg_document([o for o in local if o['id'] not in claimed], bounds=bounds, width=1600),
            (1600, 900)).save(directory/'unselected.png')
    return source


def independent_preview(source, directory):
    """Decode a separate FS preview stream; never substitute the parsed scene."""
    try:
        with olefile.OleFileIO(source) as ole:
            image, info = decode_preview(ole.openstream('FlexiSIGN Contents/Preview').read())
        image.save(directory/'embedded_preview.png')
        return {'status': 'available', 'width': image.width, 'height': image.height,
                'limitations': '内嵌预览可能陈旧或分辨率不足；仅用于独立视觉对照。'}
    except (OSError, ValueError, KeyError, IndexError, NotImplementedError):
        return {'status': 'unavailable', 'limitations': '没有可解码的独立原稿预览，无法检查共同解析遗漏。'}


def inspect_page(objects, candidates, decision, scope, page, directory, config, reference):
    from components import candidate_atlas, resolve_categories
    from vision import recognize
    source = render_evidence(objects, candidates, decision, scope, page, directory)
    candidate_atlas(page, directory/'candidates.png')
    current = {n: set(ids) for n, ids in resolve_categories(objects, candidates, decision).items()}
    metadata = [dict(id=c['id'], bbox=c['bbox'], native_type=c['native_type'],
                     path_count=len(c['member_ids']),
                     ownership={n: len(set(c['member_ids']) & ids) for n, ids in current.items()}) for c in page]
    images = [directory/'selections.png', directory/'unselected.png']
    if reference['status'] == 'available':
        root = Path(config.get('_audit_root', directory.parents[1]))
        images += [root/'embedded_preview.png', root/'parsed_page.png']
    scene = dict(objects=page, component_audit=True, audit_metadata=metadata,
                 audit_region={'id': scope['id'], 'name': scope['name']},
                 audit_images=[str(p) for p in images],
                 audit_reference_available=reference['status'] == 'available')
    scene['body_paint_check'] = bool(config.get('_body_paint_check'))
    settings = dict(config, max_candidates=len(page))
    if '_audit_deadline' in config:
        remaining = config['_audit_deadline']-time.monotonic()
        if remaining < 1:
            from vision import VisionError
            raise VisionError('检验图准备后已达到门禁总耗时上限，未再发起请求。')
        settings['timeout_seconds'] = min(float(config.get('timeout_seconds', 90)), remaining)
    return recognize(scene, source, directory/'candidates.png', settings)


def audit_components(source, objects, candidates, decision, directory, config):
    """Inspect every queued page within budget; commit only rechecked regions."""
    options = audit_options(config)
    if not options['audit_enabled']:
        return decision, {'status': 'not_run', 'reason': '配置关闭了六类门禁。'}
    from vision import VisionError
    root = Path(directory)/'candidate_audit'
    root.mkdir(parents=True, exist_ok=True)
    baseline = deepcopy(decision)
    (root/'initial_decision.json').write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding='utf8')
    reference = independent_preview(source, root)
    if reference['status'] == 'available':
        _render(svg_document(objects, width=1800), (1800, 1200)).save(root/'parsed_page.png')
    scopes = audit_regions(objects, candidates, decision)
    plans = [(r, audit_pool(objects, candidates, r)) for r in scopes]
    report = dict(status='review', source_reference=reference, requests=0, accepted_repairs=0,
                  total_pages=sum((len(p)+options['audit_page_size']-1)//options['audit_page_size'] for _, p in plans),
                  verified_pages=0, regions=[], unresolved=[],
                  source_check='not_assessed', human_reviewed=False)
    report['body_paint_transfers'] = []
    # Paint eligibility treats fill="none" as unsupported; it is nevertheless
    # valid for source wireframes/hairlines and must not flag every window mesh.
    unsupported = [o['id'] for o in objects if not o.get('d') or (
        style_capability(o) == 'unsupported'
        and not (o.get('fill') == 'none' and o.get('fill_status') not in
                 ('unsupported_appearance', 'invalid_appearance')))]
    if unsupported:
        report['unresolved'].append(dict(kind='unsupported_source', count=len(unsupported),
                                         evidence='存在未支持样式或缺少曲线的源对象，不能确认原稿完整性。'))
    deadline = time.monotonic()+options['audit_timeout_seconds']
    for region_index, (scope, pool) in enumerate(plans):
        # Pool candidates are proposals, never class assignments. Preserve them
        # in the review/report even when request budget cannot cover them all.
        known = {c['id'] for c in candidates}
        candidates.extend(c for c in pool if c['id'] not in known)
        pages = [pool[i:i+options['audit_page_size']] for i in range(0, len(pool), options['audit_page_size'])]
        before = deepcopy(decision)
        staged = deepcopy(decision)
        additions, history = [], []
        seen = {_signature(objects, candidates, staged)}
        region = dict(id=scope['id'], name=scope['name'], pages=len(pages),
                      status='review', attempts=history, committed=False)
        report['regions'].append(region)
        for round_index in range(options['audit_repair_rounds']+1):
            changed, passed = False, 0
            deferred_issues = []
            failure = None
            for page_index, page in enumerate(pages):
                remaining = deadline-time.monotonic()
                if report['requests'] >= options['audit_max_requests'] or remaining < 1:
                    failure = {'kind': 'budget', 'evidence': '达到检验请求数或总耗时上限，尚未检查的候选保留待复核。'}
                    break
                target = root/f'region_{region_index:03d}'/f'round_{round_index:02d}_page_{page_index:03d}'
                target.mkdir(parents=True, exist_ok=True)
                report['requests'] += 1
                try:
                    result = inspect_page(objects, candidates+additions, staged, scope, page, target,
                                          dict(config, _audit_deadline=deadline,
                                               timeout_seconds=min(float(config.get('timeout_seconds', 90)),
                                                                           max(1, int(remaining)))), reference)
                    validate_audit(result, {c['id'] for c in page})
                    (target/'inspection.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
                    history.append(dict(round=round_index, page=page_index, result=result))
                    check = result['source_check']
                    if check in ('mismatch', 'uncertain'):
                        report['source_check'] = check if report['source_check'] != 'mismatch' else 'mismatch'
                    elif check == 'consistent' and report['source_check'] == 'not_assessed':
                        report['source_check'] = 'consistent'
                    if result['verdict'] == 'review':
                        if check == 'mismatch':
                            failure = {'kind': 'visual_issue', 'issues': result['issues'],
                                       'evidence': '独立原稿对照发现差异，选择修复无法补画未解析内容。'}
                            break
                        # The missing motif may be on a later candidate page.
                        # Keep searching instead of stopping at its first absence.
                        deferred_issues.extend(result['issues'])
                        continue
                    if result['verdict'] == 'repair':
                        if round_index >= options['audit_repair_rounds']:
                            failure = {'kind': 'repair_limit', 'issues': result['issues'],
                                       'evidence': '达到局部修复轮数上限，已回退本区域修复。'}
                            break
                        transfer = body_paint_transfer(
                            objects, candidates+additions, staged, result['patches'],
                            {c['id'] for c in page}, {o['id'] for o in scope['objects']})
                        if transfer:
                            proposal, move = transfer
                            remaining = deadline-time.monotonic()
                            if report['requests'] >= options['audit_max_requests'] or remaining < 1:
                                failure = {'kind': 'budget', 'evidence': '车身转车漆尚未复验，保留原选择。'}
                                break
                            # Verify this move independently of pending text,
                            # emblem, and other page repairs. A later failure
                            # must not discard an already verified paint move.
                            local = candidates+[proposal]
                            verified, verified_extra = apply_patches(
                                objects, local, before, move, {proposal['id']},
                                {o['id'] for o in scope['objects']})
                            check_dir = target/'body_paint_recheck'
                            check_dir.mkdir(parents=True, exist_ok=True)
                            report['requests'] += 1
                            checked = inspect_page(
                                objects, local+verified_extra, verified, scope, [proposal], check_dir,
                                dict(config, _audit_deadline=deadline, _body_paint_check=True,
                                     _audit_root=str(root)), reference)
                            validate_audit(checked, {proposal['id']})
                            (check_dir/'inspection.json').write_text(
                                json.dumps(checked, ensure_ascii=False, indent=2), encoding='utf8')
                            if checked['source_check'] in ('mismatch', 'uncertain'):
                                if report['source_check'] != 'mismatch':
                                    report['source_check'] = checked['source_check']
                            if checked['verdict'] != 'pass' or checked['source_check'] in ('mismatch', 'uncertain'):
                                failure = {'kind': 'paint_transfer_unverified', 'issues': checked['issues'],
                                           'evidence': '车身转车漆的局部复验未通过，保留原选择。'}
                                break
                            pending, pending_extra = apply_patches(
                                objects, candidates+additions+[proposal], staged, move,
                                {proposal['id']}, {o['id'] for o in scope['objects']})
                            known = {c['id'] for c in candidates}
                            candidates.extend(c for c in [proposal]+verified_extra if c['id'] not in known)
                            before = deepcopy(verified)
                            decision = verified
                            staged = pending
                            known = {c['id'] for c in candidates+additions}
                            additions.extend(c for c in pending_extra if c['id'] not in known)
                            report['accepted_repairs'] += 1
                            report['body_paint_transfers'].append(dict(
                                region_id=scope['id'], selected_ids=list(proposal['member_ids']),
                                status='passed', outline_paths_retained=True))
                            # Body/paint entries have been replaced by the
                            # independently rechecked filled-member move.
                            result_patches = [p for p in result['patches'] if p['category'] not in ('车身', '车漆')]
                            history[-1]['paint_transfer_committed'] = True
                            if not result_patches:
                                changed = True
                                seen.add(_signature(objects, candidates+additions, staged))
                                continue
                        else:
                            result_patches = result['patches']
                        patched, extra = apply_patches(objects, candidates+additions, staged, result_patches,
                                                       {c['id'] for c in page}, {o['id'] for o in scope['objects']})
                        signature = _signature(objects, candidates+additions+extra, patched)
                        if signature in seen:
                            failure = {'kind': 'cycle', 'evidence': '修复重复了已有选择，已回退本区域修复。'}
                            break
                        seen.add(signature)
                        staged = patched; additions.extend(extra); changed = True
                        # Continue inspecting other pages against the staged
                        # result, then verify every page again in the next round.
                        continue
                    passed += 1
                except (VisionError, ValueError, OSError) as exc:
                    failure = {'kind': 'inspection_failed', 'evidence': '检验接口、协议、源对象约束或渲染失败，已回退本区域修复。',
                               'error_type': type(exc).__name__}
                    break
            if failure:
                region['problem'] = failure
                report['unresolved'].append(dict(region_id=scope['id'], **failure))
                break
            if not changed and deferred_issues:
                failure = {'kind': 'visual_issue', 'issues': deferred_issues,
                           'evidence': '检查本区域候选后仍有未解决的局部问题。'}
                region['problem'] = failure
                report['unresolved'].append(dict(region_id=scope['id'], **failure))
                break
            if not changed and passed == len(pages):
                known = {c['id'] for c in candidates}
                candidates.extend(c for c in additions if c['id'] not in known)
                decision = staged
                region.update(status='passed', committed=True, repairs=sum(
                    a['result']['verdict'] == 'repair' and (not a.get('paint_transfer_committed')
                    or any(p['category'] not in ('车身', '车漆') for p in a['result']['patches'])) for a in history))
                report['accepted_repairs'] += region['repairs']
                report['verified_pages'] += passed
                break
        if not region['committed']:
            decision = before
    if not plans:
        report['unresolved'].append(dict(kind='empty_source', evidence='没有可检查的已解析源对象。'))
    if reference['status'] != 'available' or report['source_check'] != 'consistent':
        report['unresolved'].append(dict(kind='source_unverified', evidence='独立原稿对照缺失、不确定或存在差异，解析完整性仍需核对。'))
    report['status'] = 'passed' if not report['unresolved'] else 'review'
    report['semantic_status'] = 'passed' if plans and all(r['committed'] for r in report['regions']) else 'review'
    report['unverified_pages'] = report['total_pages']-report['verified_pages']
    (root/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    return decision, report
