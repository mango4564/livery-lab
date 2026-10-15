"""Compare actual tool exports to user examples; never supplies paths to engine."""
from pathlib import Path
import hashlib
import json
import xml.etree.ElementTree as ET

from PIL import Image,ImageChops,ImageOps,ImageDraw,ImageFont
from geometry import rasterize
from reference_match import load_templates


def run():
    root=Path(__file__).resolve().parent
    destination=root/'validation/reference_exports'
    destination.mkdir(parents=True,exist_ok=True)
    templates,_=load_templates(root/'reference_library')
    by_source={t['source_sha256']:t for t in templates}
    records=[];pairs=[]
    for path in sorted((root/'output-v2').glob('*/versions/initial*/manifest.json')):
        result=json.loads(path.read_text(encoding='utf8'))
        if result['source_sha256'] not in by_source:continue
        template=by_source[result['source_sha256']]
        reference=root/'reference_library'/template['reference_name']
        expected=ET.parse(reference).getroot()
        actual=ET.parse(result['result_svg']).getroot()
        # Use exactly the same render coordinates and dimensions. Output padding
        # is a presentation choice and must not obscure a shape/colour mismatch.
        for key in ('width','height','viewBox'):actual.set(key,expected.get(key))
        actual.find('{http://www.w3.org/2000/svg}g').set('transform','scale(1,-1)')
        name=Path(result['source']).stem
        sub=destination/name;sub.mkdir(exist_ok=True)
        reference_png=sub/'reference.png';actual_png=sub/'actual.png'
        rasterize(ET.tostring(expected,encoding='unicode'),reference_png,max_width=1600)
        rasterize(ET.tostring(actual,encoding='unicode'),actual_png,max_width=1600)
        a=Image.open(reference_png).convert('RGBA');b=Image.open(actual_png).convert('RGBA')
        diff=ImageChops.difference(a,b)
        differences=sum(1 for pixel in diff.getdata() if any(pixel))
        maximum=max(channel[1] for channel in diff.getextrema())
        diff.save(sub/'difference.png')
        expected_paths=list(expected.iter('{http://www.w3.org/2000/svg}path'))
        actual_paths=list(actual.iter('{http://www.w3.org/2000/svg}path'))
        record={'source':result['source'],'reference':str(reference),'source_sha256':result['source_sha256'],
                'method':result['method'],'selected_ids':result['selected_ids'],
                'expected_path_count':len(expected_paths),'actual_path_count':len(actual_paths),
                'render_size':list(a.size),'different_pixels':differences,'max_channel_error':maximum,
                'exact_render_match':differences==0,'result_svg':result['result_svg'],'result_png':result['result_png'],
                'source_unchanged':hashlib.sha256(Path(result['source']).read_bytes()).hexdigest()==result['source_sha256']}
        records.append(record);pairs.append((name,a.copy(),b.copy(),record))
    sheet=Image.new('RGB',(1600,len(pairs)*230+45),'#eef2f6')
    draw=ImageDraw.Draw(sheet)
    try:font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',17)
    except OSError:font=ImageFont.load_default()
    draw.text((15,8),'左：用户参考    右：从 FS 实际提取（统一画布对照）',fill='#0f172a',font=font)
    for i,(name,a,b,r) in enumerate(pairs):
        y=45+i*230
        draw.text((15,y),name+'  ·  像素差异 '+str(r['different_pixels']),fill='#0f172a',font=font)
        for x,im in ((15,a),(815,b)):
            im=ImageOps.contain(im,(770,192))
            bg=Image.new('RGBA',im.size,'white');bg.alpha_composite(im)
            sheet.paste(bg.convert('RGB'),(x,y+30))
    sheet.save(destination/'reference_comparison.png')
    report={'reference_count':len(records),'exact_render_matches':sum(r['exact_render_match'] for r in records),
            'scope':'These examples are in the reference library; this is in-sample acceptance, not unseen-file accuracy.',
            'records':records}
    (destination/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k!='records'},ensure_ascii=False))
    return report


if __name__=='__main__':
    report=run()
    raise SystemExit(int(report['reference_count']!=8 or report['exact_render_matches']!=8))
