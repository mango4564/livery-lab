"""Independent integration probe; reads only original FS, never target artwork."""
import json
from pathlib import Path
import sys
import collections
import olefile

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'research/fs_parser'))
sys.path.insert(0,str(ROOT/'research/fs_attributes'))
from fs_geometry import extract_geometry
from attributes import decode_references, resolve_appearance
from geometry import transform_bounds, svg_document, rasterize


def load(source):
    doc=extract_geometry(source)
    with olefile.OleFileIO(source) as ole:
        refs=decode_references(ole)
    word=doc['geometry_stats']['tlv_word_bytes']
    for obj in doc['objects']:
        obj['id']=str(obj['object_id'])
        obj['matrix']=obj['own_matrix']
        obj['bbox_local']=obj['bbox']
        obj['bbox']=transform_bounds(obj['bbox'],obj['matrix'])
        obj['fill']=None
        try:
            appearance=resolve_appearance(bytes.fromhex(obj['appearance_hex']),refs,word)
            obj['appearance']=appearance
            solids=[a for a in appearance if a.get('color',{}).get('hex')]
            if len(solids)==1:
                obj['fill']=solids[0]['color']['hex']
        except (ValueError,KeyError) as e:
            obj['appearance_error']=str(e)
    return doc


if __name__=='__main__':
    source=Path(sys.argv[1]);dest=ROOT/'research/integration';dest.mkdir(exist_ok=True)
    doc=load(source)
    (dest/'scene.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf8')
    interesting=[]
    for o in doc['objects']:
        b=o['bbox'];w=b[2]-b[0];h=b[3]-b[1]
        if o['fill'] and o['fill'] not in ('#000000','#ffffff') and w>50 and h>0 and w/h>2:
            interesting.append({'id':o['id'],'fill':o['fill'],'bbox':b,'aspect':w/h,'class':o['class_name'],'curve':'C' in o['d'],'matrix':o['matrix']})
    print(json.dumps({'stats':doc['geometry_stats'],'fills':collections.Counter(o['fill'] for o in doc['objects']),'interesting':interesting},ensure_ascii=False,indent=2))
    objects=[o for o in doc['objects'] if o['fill']]
    svg=svg_document(objects,diagnostic=True)
    (dest/'scene.svg').write_text(svg,encoding='utf8');rasterize(svg,dest/'scene.png')
