"""Explicit, resumable live comparison of baseline Qwen, structural Qwen and Gemini.

Without --execute this only prepares source previews and a frozen comparison plan.
Credentials are loaded in memory from the ignored local profile, never exported.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import hashlib
import html
import json
import math
from pathlib import Path
import shutil
import sys
import time
from urllib import request
import uuid

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import components as comp
import vision
from geometry import rasterize, svg_document, union_bounds
from scripts.switch_ai import load_profile

PROTOCOL = 'structural-holdout-v1'
LABELS = list(comp.CATEGORIES) + ['混合装饰组', '不提取']
COMMON = (
    '你负责从车辆矢量设计稿提取可独立使用的装饰。图1原稿总览；图2本批所属完整车身；图3为带编号候选。'
    '如有图4，是移除当前待检查路径后的原车，用来对比装饰是否完整。灰色背景不是源图案。'
    '各编号可能共享路径，不要因父组含有彩条就将包含底漆或五金的整个父组都当彩条。'
    '沙漠、山丘、人物、骆驼及其构图所需的色块都可以是彩条；单色也可能是装饰，不能只凭颜色。')
INITIAL = ('完整车辆总成分为车身；局部候选若含装饰及底漆/五金不可整体提取，标混合装饰组；'
           '可完整独立提取的装饰分为彩条。')
AUDIT = ('本轮仅补查车身残留及遗漏装饰；要保留沙漠构图中的平整色带及渐变底层，前后部也可能只有单色色带。'
         '若一个局部组同时含装饰和底漆/五金标混合装饰组，后续会展开。只按可见证据判断，不要求每个视图必须有装饰。')
GUARDED = (
    '本轮同时复核前序提名的装饰和已判为车漆的路径，前序提名不是正确答案。'
    '彩条只指可独立使用的车体装饰，包括完整沙漠构图的沙丘层、人物骆驼、底层色块及前后延续的单色色带。'
    '文字、文字描边/白色衬底、logo、门徽、轮位遮罩、车窗、车灯、轮胎、五金都标不提取，不能因为属于涂装设计就归彩条。'
    '覆盖车身壳体的基础底色为车漆；单色或面积大小不能作为唯一依据。'
    '图4是移除本轮所有候选后的对照，不表示这些路径都该保留或排除。请逐编号独立判断。')


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf8')
    temp.replace(path)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode('utf8')).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def public_config(config):
    return {'model': config['model'], **vision.tuning_options(config)}


def decision_from_labels(labels):
    return {'categories': {name: [gid for gid, label in labels.items() if label == name]
                           for name in comp.CATEGORIES},
            'source_regions': [], 'source_views': {}, 'reason': PROTOCOL, 'confidence': 0.0}


def maximal_anchors(candidates, decision):
    body = [c for c in candidates if c['id'] in decision['categories']['车身']]
    return [c for c in body if not any(set(c['member_ids']) < set(a['member_ids']) for a in body)]


def owner(candidate, anchors):
    if not anchors:
        raise ValueError('No Qwen body anchor; Gemini must not supply fallback anchors')
    ids = set(candidate['member_ids'])
    anchor = max(anchors, key=lambda a: len(ids.intersection(a['member_ids']))/max(1, len(ids)))
    if ids.intersection(anchor['member_ids']):
        return anchor['id']
    b = candidate['bbox']
    x, y = (b[0]+b[2])/2, (b[1]+b[3])/2
    return min(anchors, key=lambda a:
               ((x-(a['bbox'][0]+a['bbox'][2])/2)/max(1, a['bbox'][2]-a['bbox'][0]))**2 +
               ((y-(a['bbox'][1]+a['bbox'][3])/2)/max(1, a['bbox'][3]-a['bbox'][1]))**2)['id']


def safe_signature(config):
    # The URL affects routing, but must not appear in cache files or public reports.
    return digest({'public': public_config(config), 'route': config['base_url']})


def cached_baseline(source, directory, config):
    """Run the real application pipeline; cache individual classify calls safely."""
    original = comp.recognize_groups
    source_hash = file_hash(source)

    def cached(candidates, path, cfg, **kwargs):
        path = Path(path)
        signature = digest({'protocol': PROTOCOL, 'source': source_hash,
            'implementation': [file_hash(ROOT/'components.py'), file_hash(ROOT/'vision.py')],
            'config': safe_signature(cfg), 'options': kwargs,
            'candidates': [{k: c[k] for k in ('id', 'member_ids', 'bbox', 'native_type', 'source_span')}
                           for c in candidates],
            'images': {name: file_hash(path/name) for name in
                       ('source_scene.png', 'group_atlas.png', 'group_details.png') if (path/name).exists()}})
        cache = path/'benchmark_cache.json'
        if cache.exists():
            old = read(cache)
            if old['signature'] != signature:
                raise ValueError('Cached baseline input changed; choose a new output directory')
            return old['decision']
        result = original(candidates, path, cfg, **kwargs)
        save(cache, {'signature': signature, 'decision': result})
        return result

    # Each provider runs in its own process; this hook cannot affect the other model.
    comp.recognize_groups = cached
    try:
        return comp.run(source, directory, config)
    finally:
        comp.recognize_groups = original


def validate_labels(value, allowed_ids, labels):
    if (not isinstance(value, dict) or set(value) != {'labels', 'reason', 'confidence'}
            or not isinstance(value['labels'], dict) or set(value['labels']) != set(allowed_ids)
            or any(not isinstance(x, str) or x not in labels for x in value['labels'].values())
            or not isinstance(value['reason'], str) or len(value['reason']) > 5000
            or isinstance(value['confidence'], bool) or not isinstance(value['confidence'], (int, float))
            or not math.isfinite(value['confidence']) or not 0 <= value['confidence'] <= 1):
        raise ValueError('Invalid exhaustive structural labels')
    return value


def structural_call(batch, directory, anchor, *, overview, trace_root, config,
                    stage, labels, instruction, removed=None):
    directory.mkdir(parents=True, exist_ok=True)
    comp.candidate_atlas(batch, directory/'candidates.png', columns=2)
    context = anchor['context_objects']
    rasterize(svg_document(context, width=1400, background='#bbbbbb'), directory/'owner.png')
    images = [vision._image_part(overview), vision._image_part(directory/'owner.png'),
              vision._image_part(directory/'candidates.png')]
    if removed:
        rasterize(svg_document([o for o in context if o['id'] not in removed],
                              bounds=union_bounds(context), width=1400, background='#bbbbbb'),
                  directory/'remaining.png')
        images.append(vision._image_part(directory/'remaining.png'))
    metadata = [{'id': c['id'], 'path_count': len(c['member_ids']), 'type': c['native_type'],
                 'owner': anchor['id'], 'bbox': c['bbox'], 'overlap': [x['id'] for x in batch
                   if x['id'] != c['id'] and set(c['member_ids']).intersection(x['member_ids'])]}
                for c in batch]
    prompt = (COMMON+instruction+'\n类别仅限'+json.dumps(labels, ensure_ascii=False)+
              '。对本批每一个编号恰好返回一个标签。只输出JSON：'
              '{"labels":{"编号":"类别"},"reason":"简短依据","confidence":0.0}。不添加编号。\n候选：'+
              json.dumps(metadata, ensure_ascii=False))
    parameters = {'model': config['model'], 'response_format': {'type': 'json_object'},
                  'stream': True, 'stream_options': {'include_usage': True},
                  'max_tokens': 8192, 'temperature': 0, 'top_p': 1}
    signature = digest({'protocol': PROTOCOL, 'config': safe_signature(config), 'prompt': prompt,
                        'images': [vision._image_info(i) for i in images], 'parameters': parameters,
                        'members': [c['member_ids'] for c in batch]})
    cache = directory/'benchmark_cache.json'
    if cache.exists():
        old = read(cache)
        if old['signature'] != signature:
            raise ValueError('Cached structural input changed; choose a new output directory')
        return validate_labels(old['decision'], [c['id'] for c in batch], labels)
    body = parameters | {'messages': [
        {'role': 'system', 'content': '你是车辆涂装矢量对象复核员。按可见图像和候选编号回答严格JSON。'},
        {'role': 'user', 'content': [{'type': 'text', 'text': prompt}, *images]}]}
    req = request.Request(vision._endpoint(config), data=json.dumps(body, ensure_ascii=False).encode('utf8'),
        headers={'Content-Type': 'application/json', 'Accept': 'text/event-stream',
                 'Authorization': 'Bearer '+vision.resolve_api_key(config)}, method='POST')
    rid = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'_'+uuid.uuid4().hex[:8]
    trace = {'schema_version': 1, 'id': rid, 'started_at': datetime.now(timezone.utc).isoformat(),
             'stage': stage, 'batch': str(directory.relative_to(trace_root.parent)),
             'candidate_count': len(batch), 'candidate_ids': [c['id'] for c in batch],
             'model': config['model'], 'status': 'running', 'transport': 'chat_completions',
             'parameters': parameters, 'prompt': prompt, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
             'images': [vision._image_info(i) for i in images], 'attempts': []}
    req._livery_trace = trace
    req._livery_trace_path = trace_root/(rid+'.json')
    trace_root.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    vision._save_trace(req)
    try:
        raw = vision._request_bytes(req, config['timeout_seconds'], 2*1024*1024,
                                    use_system_proxy=config.get('use_system_proxy', True))
        env = vision._response_envelope(raw)
        choice = env['choices'][0]
        trace.update(usage=vision._usage_info(env.get('usage')), finish_reason=choice.get('finish_reason'))
        if choice.get('finish_reason') != 'stop':
            raise ValueError('Unfinished structural response')
        content = choice['message']['content'].strip()
        if content.startswith('```'):
            content = '\n'.join(content.splitlines()[1:-1])
        result = validate_labels(json.loads(content), [c['id'] for c in batch], labels)
        save(cache, {'signature': signature, 'decision': result})
        save(directory/'decision.json', result)
        trace.update(status='accepted', selected_labels=result['labels'])
        print(stage, trace['batch'], 'candidates='+str(len(batch)), flush=True)
        return result
    except Exception as exc:
        trace.update(status='failed', error_type=type(exc).__name__)
        raise
    finally:
        trace['elapsed_seconds'] = round(time.perf_counter()-start, 3)
        vision._save_trace(req)
        save(trace_root.parent/'ai_requests.json', vision.request_summary(trace_root))


def run_structural(source, directory, config, bootstrap_report):
    objects, candidates, coverage = comp.prepare(source, directory/'prepared', 120)
    anchors = maximal_anchors(candidates, bootstrap_report['decision'])
    if not anchors:
        raise ValueError('Qwen did not identify complete body anchors')
    for anchor in anchors:
        b = anchor['bbox']
        ids = set(anchor['member_ids'])
        anchor['context_objects'] = [o for o in objects if o['id'] in ids or
            (b[0] <= (o['bbox'][0]+o['bbox'][2])/2 <= b[2] and
             b[1] <= (o['bbox'][1]+o['bbox'][3])/2 <= b[3])]
    kwargs = {'overview': directory/'prepared/source_scene.png', 'trace_root': directory/'ai_requests', 'config': config}
    grouped = {a['id']: [] for a in anchors}
    for candidate in candidates:
        grouped[owner(candidate, anchors)].append(candidate)
    save(directory/'grouping.json', {k: [c['id'] for c in v] for k, v in grouped.items()})
    initial = {}
    for anchor in anchors:
        pool = sorted(grouped[anchor['id']], key=lambda c: (c['bbox'][0], c['bbox'][1], -len(c['member_ids'])))
        for start in range(0, len(pool), 12):
            answer = structural_call(pool[start:start+12], directory/'initial'/anchor['id']/f'{start//12+1:02d}',
                anchor, stage='associated_initial', labels=LABELS, instruction=INITIAL, **kwargs)
            initial.update(answer['labels'])
    save(directory/'initial_labels.json', initial)
    decision = decision_from_labels(initial)
    conflicts = comp.recovery_conflicts(candidates, decision)
    safe = decision_from_labels({gid: label for gid, label in initial.items() if gid not in conflicts or label == '车身'})
    audit, total = comp.residual_surface_candidates(objects, candidates, safe, 120)
    save(directory/'audit_pool.json', [{k: c[k] for k in ('id', 'member_ids', 'bbox', 'native_type', 'body_anchor_id')} for c in audit])
    audit_labels = {}
    for anchor in anchors:
        pool = [c for c in audit if owner(c, anchors) == anchor['id']]
        for start in range(0, len(pool), 24):
            answer = structural_call(pool[start:start+24], directory/'audit'/anchor['id']/f'{start//24+1:02d}',
                anchor, stage='associated_audit', labels=['彩条', '车漆', '混合装饰组', '不提取'], instruction=AUDIT, **kwargs)
            audit_labels.update(answer['labels'])
    save(directory/'audit_labels.json', audit_labels)
    all_candidates = candidates+audit
    labels = initial | audit_labels
    protected = {i for c in all_candidates if initial.get(c['id']) in ('文字', 'logo', '门徽') for i in c['member_ids']}
    final, unresolved = set(), []
    for anchor in anchors:
        proposals = [c for c in all_candidates if labels.get(c['id']) in ('彩条', '混合装饰组', '车漆')
                     and owner(c, anchors) == anchor['id']]
        bounded = []
        for c in proposals:
            if len(c['member_ids']) > 80:
                unresolved.append({'group': c['id'], 'owner': anchor['id'], 'reason': 'whole_vehicle_not_atomic_decoration'})
            else:
                bounded.append(c)
        ids = {i for c in bounded for i in c['member_ids']}-protected
        if len(ids) > 96:
            unresolved.append({'owner': anchor['id'], 'reason': 'per_owner_path_budget', 'paths': len(ids)})
            continue
        pool = []
        for obj in objects:
            if obj['id'] not in ids:
                continue
            b = obj['bbox']
            w, h = b[2]-b[0], b[3]-b[1]
            pool.append({'id': f'P{len(pool)+1:03d}', 'members': [obj], 'member_ids': [obj['id']],
                         'bbox': b, 'native_type': 'SourcePath', 'source_span': [obj['source_offset']]*2,
                         'area': w*h, 'aspect': w/max(h, 1e-6)})
        save(directory/'local'/anchor['id']/'pool.json', [{'id': c['id'], 'member_ids': c['member_ids']} for c in pool])
        for start in range(0, len(pool), 24):
            batch = pool[start:start+24]
            answer = structural_call(batch, directory/'local'/anchor['id']/f'{start//24+1:02d}', anchor,
                stage='guarded_local', labels=['彩条', '车漆', '不提取'], instruction=GUARDED, removed=ids, **kwargs)
            final.update(i for c in batch if answer['labels'][c['id']] == '彩条' for i in c['member_ids'])
    if final & protected:
        raise ValueError('Protected source ownership changed')
    report = {'source_sha256': file_hash(source), 'status': 'experimental_review',
              'stripe_source_ids': sorted(final), 'protected_source_overlap': 0, 'unresolved': unresolved,
              'coverage': coverage | {'audit_proposals': total, 'audit_provided': len(audit)},
              'ai_request_summary': vision.request_summary(directory/'ai_requests'),
              'bootstrap_dependency': 'qwen_baseline initial classification; never Gemini',
              'bootstrap_request_ids': [r['id'] for r in bootstrap_report['ai_request_summary']['requests']
                  if r['stage'] in ('classification', 'conflict_review', 'empty_recovery')]}
    export_stripes(objects, final, directory)
    save(directory/'benchmark_report.json', report)
    return report


def export_stripes(objects, ids, directory):
    selected = [o for o in objects if o['id'] in ids]
    svg = svg_document(selected, width=1800, background='#bbbbbb')
    (directory/'stripes.svg').write_text(svg, encoding='utf8')
    rasterize(svg, directory/'stripes.png')


def run_provider(provider, plan, profiles):
    config = load_profile(Path(profiles), 'internal' if provider == 'qwen' else 'gemini')
    config.update(max_candidates=120, timeout_seconds=600, include_usage=True)
    if provider == 'qwen':
        config.update(component_batch_size=12, prompt_style='compact', temperature=0, top_p=1, max_output_tokens=8192)
    else:
        config.update(component_batch_size=0, prompt_style='detailed')
    for case in plan['cases']:
        source, base = Path(case['source']), Path(plan['output'])/case['id']
        if file_hash(source) != case['source_sha256']:
            raise ValueError('Source changed after plan freeze')
        directory = base/('qwen_baseline' if provider == 'qwen' else 'gemini')
        print(case['id'], provider, 'baseline_start', flush=True)
        try:
            objects, report = cached_baseline(source, directory, config)
            export_stripes(objects, set(report['categories']['彩条']), directory)
            save(directory/'benchmark_report.json', {
                'source_sha256': case['source_sha256'], 'status': 'ok',
                'stripe_source_ids': report['categories']['彩条'],
                'ai_request_summary': report['ai_request_summary'], 'coverage': report['coverage']})
            print(case['id'], provider, 'baseline_done', 'stripe_paths='+str(len(report['categories']['彩条'])), flush=True)
        except Exception as exc:
            save(directory/'failure.json', {'error_type': type(exc).__name__, 'stage': 'baseline',
                'ai_request_summary': vision.request_summary(directory/'ai_requests')})
            print(case['id'], provider, 'baseline_failed', type(exc).__name__, flush=True)
            if provider != 'qwen':
                continue
            # An audit/export failure must not discard a completed Qwen first
            # pass. Only its valid cached body labels can bootstrap this method.
            first_pass = list((directory/'classification').glob('*/benchmark_cache.json'))
            if not first_pass:
                continue
            merged = {name: [] for name in comp.CATEGORIES}
            for cache in first_pass:
                for name, gids in read(cache)['decision']['categories'].items():
                    merged[name].extend(gids)
            if not merged['车身']:
                continue
            report = {'decision': {'categories': merged},
                      'ai_request_summary': vision.request_summary(directory/'ai_requests')}
            print(case['id'], provider, 'bootstrap_from_valid_cached_first_pass', flush=True)
        if provider == 'qwen':
            try:
                print(case['id'], provider, 'structural_start', flush=True)
                result = run_structural(source, base/'qwen_structural', config, report)
                print(case['id'], provider, 'structural_done', 'stripe_paths='+str(len(result['stripe_source_ids'])), flush=True)
            except Exception as exc:
                save(base/'qwen_structural/failure.json', {'error_type': type(exc).__name__, 'stage': 'structural',
                    'ai_request_summary': vision.request_summary(base/'qwen_structural/ai_requests')})
                print(case['id'], provider, 'structural_failed', type(exc).__name__, flush=True)
        if file_hash(source) != case['source_sha256']:
            raise ValueError('Source changed during benchmark')


def agreement(selected, reference):
    a, b = set(selected), set(reference)
    return {'intersection': len(a & b), 'only_this': len(a-b), 'only_other': len(b-a),
            'jaccard': len(a & b)/len(a | b) if a | b else 1.0}


def visual_reference_score(selected, reference):
    """Offline reviewer annotations; alternative overlaid copies are equivalent."""
    ids = set(selected)
    allowed = set(reference['acceptable_source_ids'])
    patterns = reference['patterns']
    complete = sum(any(set(alt) <= ids for alt in p['alternatives']) for p in patterns)
    return {'complete_units': complete, 'expected_units': len(patterns),
            'missing_units': len(patterns)-complete, 'extra_source_paths': len(ids-allowed),
            'missing_paths_minimum_across_alternatives': sum(
                min(len(set(alt)-ids) for alt in p['alternatives']) for p in patterns),
            'reference_status': reference['status']}


def diagnose_initial(source, directory):
    """Preserve a failed pipeline's actual proposal without calling it a result."""
    paths = [directory/'benchmark_cache.json']
    if not paths[0].exists():
        paths = list((directory/'classification').glob('*/benchmark_cache.json'))
    if not paths:
        return None
    objects, containers = comp.load_scene(source)
    candidates, _ = comp.build_candidates(objects, containers, 120)
    byid = {c['id']: c for c in candidates}
    categories = {name: [] for name in comp.CATEGORIES}
    for path in paths:
        for name, ids in read(path)['decision']['categories'].items():
            categories[name].extend(ids)
    if any(gid not in byid for ids in categories.values() for gid in ids):
        return None
    source_ids = {i for gid in categories['彩条'] for i in byid[gid]['member_ids']}
    result = {'status': 'invalid_pipeline_raw_proposal_only', 'stripe_source_ids': sorted(source_ids),
              'source_conflict_groups': sorted(comp.recovery_conflicts(candidates, {'categories': categories}))}
    save(directory/'raw_stripes_diagnostic.json', result)
    svg = svg_document([o for o in objects if o['id'] in source_ids], width=1800, background='#bbbbbb')
    (directory/'raw_stripes_diagnostic.svg').write_text(svg, encoding='utf8')
    rasterize(svg, directory/'raw_stripes_diagnostic.png')
    return result


def summarize(plan):
    cases = []
    for case in plan['cases']:
        base = Path(plan['output'])/case['id']
        row = {'id': case['id'], 'source_name': Path(case['source']).name,
               'source_sha256': case['source_sha256'], 'source_unchanged': file_hash(case['source']) == case['source_sha256'],
               'methods': {}}
        for method in ('qwen_baseline', 'qwen_structural', 'gemini'):
            path = base/method/'benchmark_report.json'
            if not path.exists():
                data = {'status': 'not_run_or_pending'}
                failure = base/method/'failure.json'
                if failure.exists():
                    failed = read(failure)
                    data.update(status='failed', error_type=failed['error_type'])
                    summary = failed['ai_request_summary']
                    data.update(requests=summary['logical_requests'], http_attempts=summary['http_attempts'],
                                retries=summary['retries'], failed_requests=summary['failed_requests'],
                                request_stages=summary['stages'], seconds=summary['elapsed_seconds'])
                    if method != 'qwen_structural':
                        raw = diagnose_initial(case['source'], base/method)
                        if raw:
                            data['raw_proposal_only'] = raw
                row['methods'][method] = data
                continue
            report = read(path)
            summary = report['ai_request_summary']
            dependency_ids = set(report.get('bootstrap_request_ids', []))
            dependency = []
            baseline = base/'qwen_baseline/benchmark_report.json'
            if not baseline.exists():
                baseline = base/'qwen_baseline/failure.json'
            if dependency_ids and baseline.exists():
                dependency = [r for r in read(baseline)['ai_request_summary']['requests'] if r['id'] in dependency_ids]
            row['methods'][method] = {
                'status': report['status'], 'stripe_paths': len(report['stripe_source_ids']),
                'requests': summary['logical_requests'], 'http_attempts': summary['http_attempts'],
                'retries': summary['retries'], 'failed_requests': summary['failed_requests'],
                'request_stages': summary['stages'],
                'seconds': summary['elapsed_seconds'], 'bootstrap_requests': len(dependency),
                'bootstrap_http_attempts': sum(len(r.get('attempts', [])) for r in dependency),
                'bootstrap_failed_requests': sum(r['status'] == 'failed' for r in dependency),
                'including_bootstrap_seconds': round(summary['elapsed_seconds']+sum(r.get('elapsed_seconds', 0) for r in dependency), 3),
                'unresolved': report.get('unresolved', []),
                'source_ids': report['stripe_source_ids']}
        gemini = row['methods']['gemini'].get('source_ids')
        for method in ('qwen_baseline', 'qwen_structural'):
            ids = row['methods'][method].get('source_ids')
            if gemini is not None and ids is not None:
                row['methods'][method]['agreement_with_gemini_not_accuracy'] = agreement(ids, gemini)
        reference_path = base/'source_visual_reference.json'
        if reference_path.exists():
            reference = read(reference_path)
            for data in row['methods'].values():
                if 'source_ids' in data:
                    data['source_visual_review'] = visual_reference_score(data['source_ids'], reference)
                if 'raw_proposal_only' in data:
                    data['raw_proposal_source_visual_review'] = visual_reference_score(
                        data['raw_proposal_only']['stripe_source_ids'], reference)
        cases.append(row)
    result = {'protocol': PROTOCOL, 'no_independent_ground_truth': True, 'cases': cases}
    save(Path(plan['output'])/'summary.json', result)
    return result


def render_comparisons(plan):
    """Aligned source-coordinate previews; differences are not correctness labels."""
    sections = []
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 24)
    names = {'qwen_baseline': 'Qwen 原流程', 'qwen_structural': 'Qwen 工程修正版', 'gemini': 'Gemini 对照'}
    for case in plan['cases']:
        base = Path(plan['output'])/case['id']
        objects, _ = comp.load_scene(case['source'])
        bounds = union_bounds(objects)
        original = base/'source_aligned.png'
        rasterize(svg_document(objects, bounds=bounds, width=1600, background='#ffffff'), original)
        frames = [('原稿', original)]
        cards = []
        for method, name in names.items():
            report_path = base/method/'benchmark_report.json'
            if not report_path.exists():
                diagnostic = base/method/'raw_stripes_diagnostic.json'
                if not diagnostic.exists():
                    cards.append(f'<article><h3>{html.escape(name)}</h3><p>尚无通过流程校验的结果；请查看失败记录。</p></article>')
                    continue
                report = read(diagnostic)
                name += '（流程失败，仅展示原始提案）'
            else:
                report = read(report_path)
            ids = set(report['stripe_source_ids'])
            aligned = base/method/'aligned.png'
            rasterize(svg_document([o for o in objects if o['id'] in ids], bounds=bounds,
                                  width=1600, background='#bbbbbb'), aligned)
            label = f'{name} · {len(ids)} 条路径'
            if report.get('unresolved'):
                label += f' · {len(report["unresolved"])} 项待复核'
            frames.append((label, aligned))
            cards.append(f'<article><h3>{html.escape(label)}</h3><a href="{method}/aligned.png"><img src="{method}/aligned.png"></a></article>')
        loaded = [(label, Image.open(path).convert('RGB')) for label, path in frames]
        canvas = Image.new('RGB', (max(im.width for _, im in loaded),
                                   sum(im.height+48 for _, im in loaded)), '#ffffff')
        draw = ImageDraw.Draw(canvas)
        y = 0
        for label, im in loaded:
            draw.text((14, y+6), label, font=font, fill='#111827')
            canvas.paste(im, (0, y+48))
            y += im.height+48
        canvas.save(base/'comparison.png')
        title = html.escape(Path(case['source']).name)
        section = f'<h2>{title}</h2><a href="source_aligned.png"><img class="source" src="source_aligned.png"></a><div class="grid">'+''.join(cards)+'</div>'
        page = ('<!doctype html><meta charset="utf-8"><title>彩条提取对照</title><style>'
                'body{font-family:system-ui;margin:24px;background:#f1f5f9;color:#172033}'
                '.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}'
                'article{background:white;padding:12px}img{width:100%;height:auto}.source{max-width:1600px}'
                '@media(max-width:1000px){.grid{grid-template-columns:1fr}}</style>'
                '<p>点击图片查看原尺寸。三种结果使用相同原稿坐标。路径更多不代表更好；Gemini 是对照，不是真值。</p>'+section)
        (base/'comparison.html').write_text(page, encoding='utf8')
        sections.append(f'<li><a href="{case["id"]}/comparison.html">{title}</a></li>')
    (Path(plan['output'])/'comparison.html').write_text(
        '<!doctype html><meta charset="utf-8"><title>多稿件 AI 对照</title><h1>多稿件彩条提取对照</h1>'
        '<p>逐稿核对原稿与三种流程的结果。此页面不自动判定谁更准确。</p><ul>'+''.join(sections)+'</ul>', encoding='utf8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, action='append')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--plan', type=Path, help='Reuse source paths and output from an existing frozen plan')
    parser.add_argument('--profiles', type=Path, default=ROOT/'.local/ai-profiles.json')
    parser.add_argument('--execute', action='store_true', help='Explicitly permit real API requests')
    parser.add_argument('--summary-only', action='store_true')
    args = parser.parse_args(argv)
    if args.plan:
        if args.source or args.output:
            parser.error('--plan cannot be combined with --source or --output')
        existing = read(args.plan)
        args.source = [Path(c['source']) for c in existing['cases']]
        args.output = Path(existing['output'])
    if not args.source or args.output is None:
        parser.error('Provide --source and --output, or --plan')
    output = args.output.resolve()
    if output == ROOT or any(output == p.resolve().parent for p in args.source):
        parser.error('Use a dedicated benchmark output directory')
    profiles = {name: load_profile(args.profiles, name) for name in ('internal', 'gemini')}
    cases = [{'id': f'case_{i:02d}', 'source': str(p.resolve()), 'source_sha256': file_hash(p)}
             for i, p in enumerate(args.source, 1)]
    definition = {'protocol': PROTOCOL, 'cases': cases, 'output': str(output),
                  'prompt_hash': digest([COMMON, INITIAL, AUDIT, GUARDED]),
                  'profile_fingerprints': {k: safe_signature(v) for k, v in profiles.items()}}
    output.mkdir(parents=True, exist_ok=True)
    plan_path = output/'plan.json'
    if plan_path.exists():
        plan = read(plan_path)
        if plan['definition'] != definition:
            parser.error('Frozen plan differs; choose a new output directory')
    else:
        plan = {'definition': definition, 'created_at': datetime.now(timezone.utc).isoformat(),
                'script_sha256': file_hash(__file__), **definition}
        save(plan_path, plan)
    if args.summary_only:
        summarize(plan)
        render_comparisons(plan)
        return 0
    if not args.execute:
        for case in cases:
            comp.prepare(case['source'], output/case['id']/'source_preview', 120)
        print('Prepared only; no API request. Add --execute to run the frozen comparison.', flush=True)
        return 0
    with ProcessPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_provider, provider, plan, str(args.profiles.resolve()))
                   for provider in ('qwen', 'gemini')]
        for future in futures:
            future.result()
    summarize(plan)
    render_comparisons(plan)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
