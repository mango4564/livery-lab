import sys,json,olefile
from pathlib import Path
from collections import Counter
sys.path.insert(0,'livery-lab/research/fs_attributes');sys.path.insert(0,'livery-lab/research/fs_parser')
from attributes import *
from fs_geometry import extract_geometry
out=[]
for path in Path('全车矢量图素材').glob('*.fs'):
 try:
  g=extract_geometry(path)
  with olefile.OleFileIO(path) as ole:
   objects,refs=load_appearances(ole,g['objects'],g['geometry_stats']['tlv_word_bytes'])
  row={'name':path.name,'objects':len(objects),'colors':len(refs['colors']),'fill_status':dict(Counter(x['fill_status'] for x in objects))}
  if '3261754' in path.name:
   row['targets']=[{k:x[k] for k in ['object_id','fill','fill_status','appearances']} for x in objects if x['object_id'] in (233,235,237)]
  out.append(row)
 except Exception as exc:out.append({'name':path.name,'error':str(exc)})
Path('livery-lab/research/fs_attributes/attribute_corpus.json').write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf8');print(json.dumps(out,ensure_ascii=True))
