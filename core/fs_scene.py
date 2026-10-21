"""Read source display attributes for vector previews, including older results."""
from pathlib import Path
import hashlib
import re
import traceback

import olefile

from .fs_attributes import load_appearances, scan_container_ranges
from .fs_container import unpack_objects
from .fs_geometry import extract_geometry


def source_preview_objects(result, page='Page 1', *, verify=True):
    """Re-read source styles so old cached results do not keep old black fills.

    The reviewed export/selection is untouched. Only the full-scene display uses
    the newly decoded source. Source digest guards against mixing file versions.
    """
    source = Path(result['source'])
    if verify and hashlib.sha256(source.read_bytes()).hexdigest() != result['source_sha256']:
        raise ValueError('源 FS 已变化，不能与当前任务缓存混用；请重新处理该文件。')
    doc = extract_geometry(source, page=page)
    word = doc['geometry_stats']['tlv_word_bytes']
    with olefile.OleFileIO(source) as ole:
        objects, refs = load_appearances(ole, doc['objects'], word)
        raw, _ = unpack_objects(ole.openstream(f'FlexiSIGN Contents/{page}/Objects').read())
        containers = scan_container_ranges(raw, refs['classes'], word)
        containers, _ = load_appearances(ole, containers, word)
    for obj in objects:
        obj['id'] = str(obj['object_id']) + '@' + str(obj['source_offset'])
        obj['matrix'] = [1, 0, 0, 1, 0, 0]
    return compose_scene(objects, containers)


def source_preview_pages(result):
    """Enumerate actual document pages, retaining blank and failed pages."""
    source = Path(result['source'])
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != result['source_sha256']:
        raise ValueError('源 FS 已变化，请重新处理该文件。')
    with olefile.OleFileIO(source) as ole:
        pages = sorted({stream[1] for stream in ole.listdir()
                        if len(stream) == 3 and stream[0] == 'FlexiSIGN Contents'
                        and re.fullmatch(r'Page \d+', stream[1]) and stream[2] == 'Objects'},
                       key=lambda name: int(name.split()[-1]))
    if not pages:
        raise ValueError('FS 中没有找到文档页面。')
    previews = []
    for name in pages:
        try:
            objects = source_preview_objects(result, page=name, verify=False)
            previews.append({'name':name, 'objects':objects, 'error':None})
        except Exception as exc:
            previews.append({'name':name, 'objects':[], 'error':str(exc),
                             'error_type':type(exc).__name__, 'error_details':traceback.format_exc()})
    if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
        raise ValueError('读取期间源 FS 已变化，请重新处理该文件。')
    return previews


def compose_scene(objects, containers):
    """Preserve stored order; compounds use their parent's even-odd fill.

    Confirmed by AGroup::draw and AComposite::draw in the official fscore.dll;
    see research/native_rendering/FINDINGS.md. Byte spans identify ownership,
    while matrices remain unapplied because path geometry is in page space.
    """
    roots, stack = [], []
    nodes = [(obj['source_offset'], obj, False) for obj in objects]
    nodes += [(obj['source_offset'], obj, True) for obj in containers]
    for offset, obj, is_container in sorted(nodes, key=lambda item: item[0]):
        while stack and offset >= stack[-1][0]['end_offset']:
            stack.pop()
        children = []
        entry = (obj, children)
        (stack[-1][1] if stack else roots).append(entry)
        if is_container:
            stack.append(entry)

    def emit(entry):
        obj, children = entry
        if obj.get('object_flags', 0) & 0x08000000:
            return []
        if not children:
            return [obj] if obj.get('d') else []
        members = [path for child in children for path in emit(child)]
        if obj.get('effect_result_span'):
            start, end = obj['effect_result_span']
            return [path for path in members if start <= path['source_offset'] < end]
        if obj['class_name'] == 'AComposite' and members:
            from geometry import union_bounds
            combined = dict(obj, id='compound-' + str(obj['object_id']) + '@' + str(obj['source_offset']),
                            d=' '.join(path['d'] for path in members), bbox=union_bounds(members),
                            matrix=[1, 0, 0, 1, 0, 0])
            return [combined]
        return members

    return [obj for entry in roots for obj in emit(entry)]
