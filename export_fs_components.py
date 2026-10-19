"""Reproduce the reviewed five-category extraction for the 3261754 sample.

Category spans are annotations of this exact source, not a general classifier.
All exported curves and paints are read from FS; reference art is never copied.
"""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageFont

from core.fs_scene import source_preview_objects
from geometry import rasterize, svg_document
from reference_match import match_motifs


SOURCE_SHA256 = 'fb274b2fb319457eeb474587fa8c6e437e539b1c361c158ec85af7d92eead412'
SPANS = {'车身': (58519, 1224554), '文字': (1420356, 1555357),
         'logo': (1325587, 1420350), '门徽': (1244453, 1325587)}


def export(source, destination):
    source, destination = Path(source), Path(destination)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError('此分类配置仅适用于已核对的 3261754 样本；其他文件需要重新标注。')
    objects = source_preview_objects({'source': str(source), 'source_sha256': digest})
    motif = match_motifs(objects, Path(__file__).parent / 'reference_library', digest)
    if (set(motif.get('selected_ids', [])) != {'225@54782', '227@55309', '229@55836'}
            or motif.get('layout_residual', 1) > 1e-6):
        raise ValueError('源彩条完整组合未匹配，停止导出。')
    categories = {name: [o for o in objects if lo <= o['source_offset'] < hi]
                  for name, (lo, hi) in SPANS.items()}
    categories['车身'] = [o for o in objects if o['object_id'] == 221] + categories['车身']
    categories['彩条'] = [o for o in objects if o['id'] in motif['selected_ids']]
    order = ['车身', '彩条', '文字', 'logo', '门徽']
    if any(not categories[name] for name in order):
        raise ValueError('分类为空，停止导出。')
    destination.mkdir(parents=True, exist_ok=True)
    report = {'source': str(source.resolve()), 'source_sha256': digest,
              'classification': 'reviewed sample-specific source spans; stripe geometry matching',
              'page': 'Page 1', 'categories': {},
              'limitations': ['仅当前样本的已核对单侧素材，未实现任意 FS 五类自动识别。',
                              '文字和门徽为原始轮廓路径，非可编辑文本。',
                              'ACircle、ARectangle 等其他原语尚未全部解码；不承诺细节逐像素一致。',
                              '物理尺寸及下料比例未验证。']}
    sheet = Image.new('RGB', (1600, 1320), '#e5e7eb')
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 23)
    for i, name in enumerate(order):
        selected = categories[name]
        svg = svg_document(selected, width=2400)
        root = ET.fromstring(svg)
        if len(list(root.iter('{http://www.w3.org/2000/svg}path'))) != len(selected):
            raise ValueError('导出路径数量不一致')
        (destination / (name + '.svg')).write_text(svg, encoding='utf-8')
        rasterize(svg, destination / (name + '.png'), max_width=2400)
        with Image.open(destination / (name + '.png')) as raw:
            raw = raw.convert('RGBA')
            if raw.getextrema()[3][0] != 0 or not raw.getbbox():
                raise ValueError('PNG 透明通道或内容异常')
            preview = Image.new('RGBA', raw.size, '#555555' if name == '门徽' else '#ffffff')
            preview.alpha_composite(raw)
            preview.convert('RGB').save(destination / (name + '_预览.png'))
            preview.thumbnail((1540, 205))
            draw.text((30, i * 264 + 8), name, font=font, fill='#111827')
            sheet.paste(preview.convert('RGB'), ((1600-preview.width)//2, i*264+49))
        report['categories'][name] = {
            'path_count': len(selected), 'source_ids': [o['id'] for o in selected],
            'unknown_paint_ids': [o['id'] for o in selected if o.get('fill_status') == 'unknown'],
            'files': [name + '.svg', name + '.png', name + '_预览.png']}
    sheet.save(destination / '五类提取汇总.png')
    if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
        raise ValueError('导出期间源文件发生变化')
    report['source_unchanged'] = True
    (destination / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({name: report['categories'][name]['path_count'] for name in order}, ensure_ascii=False))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    export(args.source, args.out)
