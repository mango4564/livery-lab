"""Isolated FS geometry extraction, candidate selection, and reviewable exports."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import shutil
import sys
import time
import uuid
from datetime import datetime

import olefile
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parent


def _code_digest():
    digest=hashlib.sha256()
    inputs=([Path(sys.executable)] if getattr(sys,'frozen',False) else
            [ROOT/'engine.py',ROOT/'components.py',ROOT/'reviewed_exports.py',ROOT/'geometry.py',ROOT/'vector_preview.py',ROOT/'recognition.py',ROOT/'vision.py',ROOT/'credentials.py',ROOT/'runtime.py',
             *list((ROOT/'core').glob('*.py'))])
    for path in sorted(inputs):
        digest.update(path.name.encode('utf8'))
        digest.update(path.read_bytes())
    return digest.hexdigest()


# Check both sides of local imports, not only files read after modules loaded.
_IMPORT_CODE_DIGEST=_code_digest()

from core.fs_container import decode_preview, unpack_objects
from core.fs_geometry import extract_geometry
from core.fs_attributes import load_appearances, scan_container_ranges
from geometry import IDENTITY, candidate_image, candidate_sheet, rasterize, svg_document, union_bounds
from recognition import rank_candidates, candidate_groups
from vision import read_config, recognize, VisionError
from runtime import APP_DIR, FROZEN

VERSION='0.3.0-independent-source-paths'
if _code_digest()!=_IMPORT_CODE_DIGEST:
    raise RuntimeError('加载提取引擎期间程序代码发生变化，请重新启动工具。')


def _write_json(path, data):
    path=Path(path)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
        os.replace(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def _fingerprint(options):
    code=_code_digest()
    if code!=_IMPORT_CODE_DIGEST:
        raise RuntimeError('运行中的引擎代码已发生变化，请重新启动工具后处理，避免混用版本。')
    config=read_config()
    # Never persist secrets, even hashed credentials, in manifests or cache keys.
    selected={k:config.get(k) for k in ('enabled','base_url','model','max_candidates','use_system_proxy')}
    return hashlib.sha256((code+json.dumps({'use_ai':bool(options.get('use_ai')), 'config':selected,
        'extraction_mode':options.get('extraction_mode','stripes'),
        'recognition_policy':'independent_no_references'},sort_keys=True)).encode()).hexdigest()


def _resolve_reference_directory(source,options):
    # Legacy options remain readable, but extraction never opens a library.
    return ''


def _check_pipeline(fingerprint,use_ai,reference_directory='',extraction_mode='stripes'):
    if _fingerprint({'use_ai':use_ai,'reference_directory':reference_directory,'extraction_mode':extraction_mode})!=fingerprint:
        raise RuntimeError('本次处理期间程序代码或 AI 配置发生变化，拒绝提交混合版本；请重试。')


def _file_sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def _source_key(source,digest,fingerprint):
    canonical=os.path.normcase(str(Path(source).resolve()))
    return hashlib.sha256(json.dumps([canonical,digest,fingerprint],ensure_ascii=False).encode('utf8')).hexdigest()


def _artifact_paths(result):
    paths=[result[k] for k in ('preview_png','candidate_png','result_png','result_svg')]
    if result.get('report_json'):
        paths.append(result['report_json'])
    if result.get('candidate_sheet_png'):
        paths.append(result['candidate_sheet_png'])
    for view in result.get('views',[]):
        paths.extend(view[k] for k in ('svg','png'))
    return [Path(p).resolve() for p in paths]


def _seal_version(result,directory):
    """Finish the immutable version before any latest pointer can reference it."""
    directory=Path(directory).resolve()
    result['output_dir']=str(directory)
    result['version_manifest']=str(directory/'manifest.json')
    result['report_json']=str(directory/'report.json')
    result.pop('artifact_hashes',None)
    summary={k:v for k,v in result.items() if k!='objects'}
    summary['object_count']=len(result['objects'])
    _write_json(directory/'report.json',summary)
    artifacts=_artifact_paths(result)
    if any(not p.is_relative_to(directory) for p in artifacts):
        raise ValueError('版本成果必须全部位于独立版本目录内。')
    result['artifact_hashes']={str(p):_file_sha(p) for p in artifacts}
    _write_json(directory/'manifest.json',result)


def _load_version(pointer):
    """Validate pointer, immutable manifest and every referenced artifact."""
    path=Path(pointer['version_manifest']).resolve()
    if _file_sha(path)!=pointer['manifest_sha256']:
        raise ValueError('版本 manifest 摘要不一致。')
    result=json.loads(path.read_text(encoding='utf8'))
    directory=path.parent
    if Path(result['output_dir']).resolve()!=directory or Path(result['version_manifest']).resolve()!=path:
        raise ValueError('版本目录与 manifest 不一致。')
    paths=_artifact_paths(result)
    hashes=result['artifact_hashes']
    if set(map(str,paths))!=set(hashes):
        raise ValueError('版本成果清单不一致。')
    for artifact in paths:
        if not artifact.is_relative_to(directory) or _file_sha(artifact)!=hashes[str(artifact)]:
            raise ValueError('版本成果缺失或内容已经变化。')
    ids=[obj['id'] for obj in result['objects']]
    selected=result['selected_ids']
    if len(ids)!=len(set(ids)) or len(selected)!=len(set(selected)) or not set(selected)<=set(ids):
        raise ValueError('缓存对象 ID 或选择不一致。')
    return result


def _commit_version(result):
    path=Path(result['version_manifest'])
    pointer={'version_manifest':str(path),'manifest_sha256':_file_sha(path)}
    verified=_load_version(pointer)
    run=Path(verified['run_dir'])
    if path.parent.parent.parent!=run:
        raise ValueError('版本不在预期运行目录中。')
    index=Path(verified['cache_index'])
    index.parent.mkdir(parents=True,exist_ok=True)
    # This is the publication boundary: older versions remain immutable.
    _write_json(run/'manifest.json',pointer)
    _write_json(index,{'run_manifest':str(run/'manifest.json'),
                       'source':os.path.normcase(str(Path(verified['source']).resolve())),
                       'source_sha256':verified['source_sha256'],
                       'pipeline_fingerprint':verified['pipeline_fingerprint']})
    return verified


def _cached_result(index,source,digest,fingerprint):
    try:
        entry=json.loads(index.read_text(encoding='utf8'))
        canonical=os.path.normcase(str(source.resolve()))
        if (entry['source'],entry['source_sha256'],entry['pipeline_fingerprint'])!=(canonical,digest,fingerprint):
            return None
        pointer_path=Path(entry['run_manifest'])
        result=_load_version(json.loads(pointer_path.read_text(encoding='utf8')))
        if (os.path.normcase(str(Path(result['source']).resolve())),result['source_sha256'],result['pipeline_fingerprint'])!=(canonical,digest,fingerprint):
            return None
        if Path(result['run_dir'])/'manifest.json'!=pointer_path or Path(result['cache_index'])!=index:
            return None
        return result
    except (OSError,ValueError,KeyError,TypeError):
        return None


def _slug(source):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',source.stem).strip(' .')[:80] or 'FS'


def process_file(source, output_root, options=None, progress=None):
    """Controller API: each source is decoded in a disposable child process."""
    source=Path(source).resolve();output_root=Path(output_root).resolve()
    options=dict(options or {})
    if options.get('extraction_mode', 'stripes') not in ('stripes', 'components'):
        raise ValueError('未知提取模式。')
    options['reference_directory']=_resolve_reference_directory(source,options)
    report=progress or (lambda message: None)
    if source.suffix.lower()!='.fs' or not source.is_file():
        raise ValueError('请选择存在的 FS 文件。')
    if source.stat().st_size>256*1024*1024:
        raise ValueError('当前版本暂不处理超过 256 MB 的 FS。')
    digest=_file_sha(source)
    fingerprint=_fingerprint(options)
    key=_source_key(source,digest,fingerprint)
    index=output_root/'.cache'/(key+'.json')
    if not options.get('force'):
        old=_cached_result(index,source,digest,fingerprint)
        if old is not None:
            report('复用已校验完整性的最新版本（保留人工修正）。')
            return old
    run=output_root/(_slug(source)+'_'+digest[:8]+'_'+key[:8]+'_run_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_'+uuid.uuid4().hex[:6])
    base=run/'versions'/('initial_'+uuid.uuid4().hex[:12])
    base.mkdir(parents=True,exist_ok=False)
    _write_json(base/'request.json',{'reference_directory':options['reference_directory'],
                                  'extraction_mode':options.get('extraction_mode','stripes')})
    report('读取 FS 原始曲线和颜色引用（独立工作进程）。')
    python=Path(sys.executable)
    if python.name.lower()=='pythonw.exe':
        python=python.with_name('python.exe')
    worker_args=['_worker',str(source),str(base),digest,fingerprint,'1' if options.get('use_ai') else '0']
    cmd=([str(python)]+worker_args) if FROZEN else ([str(python),'-X','utf8',str(ROOT/'engine.py')]+worker_args)
    timeout=float(options.get('timeout_seconds',240))
    try:
        child=subprocess.run(cmd,capture_output=True,encoding='utf8',errors='replace',
                             timeout=timeout,cwd=ROOT,
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except subprocess.TimeoutExpired:
        _write_json(base/'failure.json',{'source':str(source),'source_sha256':digest,'error':'timeout','timeout_seconds':timeout})
        raise RuntimeError(f'该文件超过 {timeout:g} 秒，工作进程已终止，其他文件可继续。') from None
    if child.returncode or not (base/'manifest.json').is_file():
        failure=base/'failure.json'
        reason='FS 工作进程异常退出。'
        if failure.exists():
            reason=json.loads(failure.read_text(encoding='utf8')).get('error',reason)
        else:
            _write_json(failure,{'source':str(source),'source_sha256':digest,'error':reason,'exit_code':child.returncode})
        raise RuntimeError(reason)
    result=json.loads((base/'manifest.json').read_text(encoding='utf8'))
    _check_pipeline(fingerprint,bool(options.get('use_ai')),options['reference_directory'],options.get('extraction_mode','stripes'))
    if _file_sha(source)!=digest:
        raise RuntimeError('处理过程中源文件发生变化，拒绝提交该次结果。')
    result=_commit_version(result)
    report(f'解析 {len(result["objects"])} 条曲线，{len(result["selected_ids"])} 条进入当前结果；状态：待复核。')
    return result


def _placeholder(path, text):
    image=Image.new('RGBA',(1200,240),(255,255,255,0))
    draw=ImageDraw.Draw(image)
    draw.text((25,100),text,fill='#64748b')
    image.save(path)


def _render_selection(result, ids, directory):
    all_objects={o['id']:o for o in result['objects']}
    if len(ids)!=len(set(ids)) or any(i not in all_objects for i in ids):
        raise ValueError('所选对象 ID 无效或重复。')
    chosen=set(ids)
    selected=[o for o in result['objects'] if o['id'] in chosen]
    # Reconstruct a verified multi-subpath AComposite using its own FS leaves.
    # User reference coordinates are never used to construct export geometry.
    replacements={};consumed=set()
    for group in result.get('reference_match',{}).get('export_groups',[]):
        ids=group['source_ids']
        if not set(ids)<=chosen:continue
        leaves=[all_objects[i] for i in ids]
        common=set(leaves[0].get('compound_container_ids',[]))
        for leaf in leaves[1:]:common.intersection_update(leaf.get('compound_container_ids',[]))
        if not common or len({leaf.get('fill') for leaf in leaves})!=1:continue
        ordered=[o for o in selected if o['id'] in set(ids)]
        composite=dict(ordered[0],id='composite-'+str(min(common)),d=' '.join(o['d'] for o in ordered),bbox=union_bounds(ordered))
        replacements[ordered[0]['id']]=composite
        consumed.update(o['id'] for o in ordered[1:])
        consumed.discard(ordered[0]['id'])
    selected=[replacements.get(o['id'],o) for o in selected if o['id'] not in consumed]
    svg_path=directory/'result.svg';png_path=directory/'result.png'
    svg=svg_document(selected)
    # Machine-readable source association is separate from dimensions: no mm or
    # physical-size claim is introduced by a pixel-width SVG preview.
    import xml.etree.ElementTree as ET
    root=ET.fromstring(svg)
    metadata=root.find('{http://www.w3.org/2000/svg}metadata')
    metadata.text=json.dumps({'source_sha256':result['source_sha256'],
        'selected_ids':ids,'method':result['method'],
        'geometry':'FS APath/AEditPath source coordinates',
        'transform_semantics':'source coordinate export; stored authoring matrix not applied',
        'physical_scale_verified':False,
        'unknown_fill_ids':[o['id'] for o in selected if not o.get('fill')]},ensure_ascii=False)
    # Keep default namespace without ns0 prefixes for broad vector-tool support.
    ET.register_namespace('','http://www.w3.org/2000/svg')
    svg=ET.tostring(root,encoding='unicode')
    svg_path.write_text(svg,encoding='utf8')
    if selected:
        rasterize(svg,png_path,max_width=1600)
    else:
        _placeholder(png_path,'No objects selected. Review candidates and select paths.')
    return str(svg_path),str(png_path)


def export_selection(result, selected_ids, output_dir=None, *, category_name=None):
    """Explicit user review: only choose existing source paths, never redraw."""
    result=dict(result)
    valid={o['id'] for o in result['objects']}
    if not selected_ids or len(selected_ids)!=len(set(selected_ids)) or any(i not in valid for i in selected_ids):
        raise ValueError('请至少选择一个有效对象，不能使用未知或重复 ID。')
    if result.get('extraction_mode') == 'components':
        if category_name not in result['component_categories']:
            raise ValueError('请先在候选下拉框切换到要修正的类别，再保存该类。')
        categories={name:list(ids) for name,ids in result['component_categories'].items()}
        others={oid for name,ids in categories.items() if name not in (category_name,'车身') for oid in ids}
        if set(selected_ids) & others:
            raise ValueError('所选对象已属于其他非车身类别，请先排除重叠对象。')
        categories[category_name]=list(selected_ids)
        if category_name!='车身':
            categories['车身']=[oid for oid in categories['车身'] if oid not in set(selected_ids)]
        result['component_categories']=categories
        result['confirmed_categories']=sorted(set(result.get('confirmed_categories',[])) | {category_name})
        union={oid for ids in categories.values() for oid in ids}
        selected_ids=[obj['id'] for obj in result['objects'] if obj['id'] in union]
    old_directory=Path(result['output_dir']).resolve()
    # output_dir is an optional new run destination, never an overwrite target.
    run=Path(output_dir).resolve() if output_dir else Path(result.get('run_dir',old_directory)).resolve()
    directory=run/'versions'/('review_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_'+uuid.uuid4().hex[:8])
    directory.mkdir(parents=True,exist_ok=False)
    result['run_dir']=str(run)
    if 'cache_index' not in result:
        key=_source_key(result['source'],result['source_sha256'],result['pipeline_fingerprint'])
        result['cache_index']=str(run.parent/'.cache'/(key+'.json'))
    # Copy all auxiliary files into this snapshot, including spatial previews.
    for key,name in (('preview_png','preview.png'),('candidate_png','candidates.png')):
        target=directory/name
        shutil.copy2(result[key],target)
        result[key]=str(target)
    if result.get('candidate_sheet_png'):
        target=directory/'candidate_tiles.png'
        shutil.copy2(result['candidate_sheet_png'],target)
        result['candidate_sheet_png']=str(target)
    result['views']=[dict(view) for view in result.get('views',[])]
    for i,view in enumerate(result['views'],1):
        local=directory/'candidate_groups'/f'{i:02d}'
        local.mkdir(parents=True)
        for key,suffix in (('svg','.svg'),('png','.png')):
            target=local/('result'+suffix)
            shutil.copy2(view[key],target)
            view[key]=str(target)
    result['selected_ids']=list(selected_ids)
    if result.get('extraction_mode') == 'components':
        for view in result['views']:
            name=view['category']
            ids=result['component_categories'][name]
            local=dict(result,reference_match={})
            view['svg'],view['png']=_render_selection(local,ids,Path(view['svg']).parent)
            view['selected_ids']=list(ids)
            view['bbox']=union_bounds([o for o in result['objects'] if o['id'] in set(ids)])
    result['result_svg'],result['result_png']=_render_selection(result,result['selected_ids'],directory)
    result.update(output_dir=str(directory),human_confirmed=True,status='ready',
                  review_note='用户已确认对象选择；尺寸、裁剪及复合操作仍按报告限制。')
    if result.get('extraction_mode') == 'components':
        result['status']='review'
        result['human_confirmed']=False
        result['review_note']='已保存该类修正；其他类别和缺失类别仍需逐项核对。'
    chosen=set(selected_ids)
    unknown=[o['id'] for o in result['objects'] if o['id'] in chosen and not o.get('fill')]
    if unknown:
        result['status']='review'
        result['warnings']=list(result['warnings'])+['所选对象 '+','.join(unknown)+' 的原色未知，SVG/PNG 使用灰色诊断色，请勿视为原色输出。']
    _seal_version(result,directory)
    return _commit_version(result)


def _run_components_worker(source,directory,digest,fingerprint,use_ai,reference_directory):
    from components import run
    if not use_ai:
        raise ValueError('五类识别需要在 AI 接口配置中启用视觉模型；也可切回彩条模式。')
    started=time.monotonic()
    objects, components_report=run(source,directory,read_config())
    categories=components_report['categories']
    chosen={oid for ids in categories.values() for oid in ids}
    for obj in objects:
        obj['score']=1.0 if obj['id'] in chosen else 0.0
    run_dir=directory.parent.parent
    key=_source_key(source,digest,fingerprint)
    result={'version':VERSION,'pipeline_fingerprint':fingerprint,'source':str(source),
            'source_sha256':digest,'run_dir':str(run_dir),
            'cache_index':str(run_dir.parent/'.cache'/(key+'.json')),
            'output_dir':str(directory),'status':'review','method':'vision_five_components',
            'extraction_mode':'components','physical_scale_verified':False,'human_confirmed':False,
            'reference_library_used':False,
            'objects':objects,'selected_ids':[o['id'] for o in objects if o['id'] in chosen],
            'suggested_ids':[],'candidate_count':len(chosen),
            'preview_png':str(directory/'source_scene.png'),
            'candidate_png':str(directory/'group_atlas.png'),
            'views':components_report['views'], 'component_categories':categories,
            'component_candidates':components_report.get('candidates',[]),
            'component_coverage':components_report['coverage'],
            'ai_result':components_report['decision'],
            'warnings':['五类为 AI 组合识别候选，须逐类核对；模型自报置信度不是准确率。',
                        '未识别的类别保持为空；候选有数量上限，可能漏掉拆散、转曲或未解码对象。',
                        '车身只扣除已分类的重叠子对象，未识别的运营元素仍可能混入。',
                        '当前解码 APath、AEditPath、AOval；其他原语、纹理及部分字体/裁剪效果可能缺失。',
                        '当前仅提取第1页。物理比例未验证。'],
            'elapsed_seconds':round(time.monotonic()-started,3)}
    result['result_svg'],result['result_png']=_render_selection(result,result['selected_ids'],directory)
    if _file_sha(source)!=digest:
        raise RuntimeError('处理过程中源文件发生变化。')
    _check_pipeline(fingerprint,use_ai,reference_directory,'components')
    _seal_version(result,directory)
    return result


def _run_worker(source,directory,digest,fingerprint,use_ai):
    request_path=directory/'request.json'
    request=json.loads(request_path.read_text(encoding='utf8')) if request_path.is_file() else {}
    reference_directory=_resolve_reference_directory(source,request)
    extraction_mode=request.get('extraction_mode','stripes')
    _check_pipeline(fingerprint,use_ai,reference_directory,extraction_mode)
    if extraction_mode == 'components':
        return _run_components_worker(source,directory,digest,fingerprint,use_ai,reference_directory)
    run=directory.parent.parent
    key=_source_key(source,digest,fingerprint)
    started=time.monotonic()
    doc=extract_geometry(source)
    if not doc['objects']:
        raise ValueError('未找到当前版本支持的 APath / AEditPath 原始曲线。')
    word=doc['geometry_stats']['tlv_word_bytes']
    warnings=[
        '当前为需复核的原始曲线提取版本，候选评分未经独立数据集校准。',
        '提取对象来自第 1 页；整车原图预览可查看全部页面。提取仍可能包含不可见的编辑对象。',
        '保留 FS 源曲线坐标；存储矩阵的语义及父子变换尚未验证，不自动累乘。',
        'APath 闭合标志尚未完全解释；SVG 填充规则暂为 evenodd，复杂孔洞需复核。',
        '物理比例未验证。SVG 像素宽度不代表毫米，不能据此直接确定下料尺寸。']
    with olefile.OleFileIO(source) as ole:
        objects,refs=load_appearances(ole,doc['objects'],word)
        raw,_=unpack_objects(ole.openstream('FlexiSIGN Contents/Page 1/Objects').read())
        containers=scan_container_ranges(raw,refs['classes'],word)
        try:
            image,preview_info=decode_preview(ole.openstream('FlexiSIGN Contents/Preview').read())
            image.save(directory/'preview.png')
        except Exception:
            preview_info={'unavailable':True}
            _placeholder(directory/'preview.png','Embedded FS preview unavailable.')
            warnings.append('FS 内嵌预览无法解码；请以原软件核对设计。')
        page_count=len([p for p in ole.listdir() if len(p)==3 and p[0]=='FlexiSIGN Contents'
                        and re.fullmatch(r'Page \d+',p[1]) and p[2]=='Objects'])
        if page_count>1:
            warnings.append(f'本文件有 {page_count} 个文档页，整车预览可逐页查看；本次彩条提取对象来自第 1 页。')
    import heapq
    seen=set();active={};endings=[];container_pos=0
    for obj in objects:
        oid=str(obj['object_id'])
        # Byte offset disambiguates IDs in formats containing repeated snapshots.
        if oid in seen:
            oid+='@'+str(obj['source_offset'])
        seen.add(oid);obj['id']=oid
        obj['matrix']=list(IDENTITY)
        obj['stored_matrix_unapplied']=obj.pop('own_matrix')
        # Spatially scoped source-byte intervals, not an invented transform tree.
        pos=obj['source_offset']
        while container_pos<len(containers) and containers[container_pos]['source_offset']<pos:
            c=containers[container_pos]
            if c['end_offset']>pos:
                active[container_pos]=c
                heapq.heappush(endings,(c['end_offset'],container_pos))
            container_pos+=1
        while endings and endings[0][0]<=pos:
            _,expired=heapq.heappop(endings);active.pop(expired,None)
        ancestry=list(active.values())
        obj['container_ids']=[c['object_id'] for c in ancestry]
        obj['compound_container_ids']=[c['object_id'] for c in ancestry if c['class_name']=='AComposite']
        obj['requires_review_geometry']=any(c['requires_boolean_semantics'] for c in ancestry)
        # Keep compact provenance; the full raw source is always the authority.
        for attr_key in ('appearance_hex','appearances','tree_path'):
            obj.pop(attr_key,None)
    bounds=rank_candidates(objects,containers)
    groups=candidate_groups(objects,containers)
    native_top_match = bool(groups and groups[0].get('is_native_group') and groups[0].get('mean_score', 0) >= 0.8)
    auto_selected = list(groups[0]['selected_ids']) if native_top_match else []
    method_name = 'native_group_matched' if native_top_match else 'unresolved_review'
    result={'version':VERSION,'pipeline_fingerprint':fingerprint,'source':str(source),
            'run_dir':str(run),'cache_index':str(run.parent/'.cache'/(key+'.json')),
            'source_sha256':digest,'output_dir':str(directory),'status':'review',
            'method':method_name,'physical_scale_verified':False,
            'reference_library_used':False,
            'human_confirmed':False,'objects':objects,'selected_ids':auto_selected,
            'suggested_ids':groups[0]['selected_ids'] if groups else [],
            'geometry_stats':doc['geometry_stats'],'preview_info':preview_info,
            'warnings':warnings,'preview_png':str(directory/'preview.png'),
            'candidate_png':str(directory/'candidates.png'),'views':[],
            'candidate_count':sum(o['score']>0 for o in objects)}
    config=read_config()
    # The same exact bounded pool is used for both numbered preview and model
    # metadata. Neutral, white and small components are not hard-excluded.
    pool_limit=min(config['max_candidates'],150)
    candidates=sorted(objects,key=lambda o:o.get('score',0),reverse=True)[:pool_limit]
    result['ai_candidate_ids']=[o['id'] for o in candidates]
    result['ai_omitted_count']=max(0,len(objects)-len(candidates))
    if candidates:
        candidate_image(candidates,directory/'candidates.png',bounds=bounds,
                        max_labels=config['max_candidates'])
        candidate_sheet(candidates,directory/'candidate_tiles.png')
        result['candidate_sheet_png']=str(directory/'candidate_tiles.png')
    else:
        _placeholder(directory/'candidates.png','No strong colour candidates. Use Show all objects to review.')
        warnings.append('规则未找到明显彩条候选，请显示全部对象后手动选择。')
    if native_top_match:
        warnings.append('已自动匹配到 FlexiSIGN 原生高置信度组合 (' + groups[0].get('label', '') + ')；可在下拉菜单切换其他候选。')
    else:
        warnings.append('未找到高置信原生组合；请使用 AI 或手动复核独立源曲线。')
    if use_ai:
        try:
            model_result=recognize({'objects':candidates,'candidate_sheet_png':result.get('candidate_sheet_png')},directory/'preview.png',directory/'candidates.png',config)
            result['selected_ids']=model_result['selected_ids']
            result['method']='vision_selected_source_paths'
            result['ai_result']=model_result
            result['ai_model']=config['model']
            warnings.append('AI 的置信度未经校准，仍需结合原图检查漏选与多选。')
        except VisionError as exc:
            result['selected_ids']=[]
            result['method']='ai_failed_no_selection'
            result['ai_error']=str(exc)
            warnings.append('AI 未完成：'+str(exc)+' 规则候选仅供手动复核。')
    # Each spatial group has its own preview, so mirrored sides/front/rear are
    # inspectable separately. Groups are candidates, not verified view labels.
    for i,group in enumerate(groups[:30],1):
        sub=directory/'candidate_groups'/f'{i:02d}'
        sub.mkdir(parents=True)
        local=dict(result,method='rules_native_group' if group.get('is_native_group') else 'rules_spatial_group')
        a,b=_render_selection(local,group['selected_ids'],sub)
        lbl = group.get('label', f'候选组合 {i}')
        result['views'].append({'label':f'{lbl} (第{i}组)','selected_ids':group['selected_ids'],'bbox':group['bbox'],'svg':a,'png':b})
    result['result_svg'],result['result_png']=_render_selection(result,result['selected_ids'],directory)
    if _file_sha(source)!=digest:
        raise RuntimeError('处理过程中源文件发生变化，拒绝保存该次结果。')
    _check_pipeline(fingerprint,use_ai,reference_directory)
    result['elapsed_seconds']=round(time.monotonic()-started,3)
    _seal_version(result,directory)
    return result


if __name__=='__main__':
    if len(sys.argv)==7 and sys.argv[1]=='_worker':
        source=Path(sys.argv[2]);directory=Path(sys.argv[3])
        try:
            _run_worker(source,directory,sys.argv[4],sys.argv[5],sys.argv[6]=='1')
        except Exception as exc:
            _write_json(directory/'failure.json',{'source':str(source),'source_sha256':sys.argv[4],
                'error':type(exc).__name__+': '+str(exc)})
            sys.exit(2)
    else:
        raise SystemExit('Use cli.py or ui.py.')
