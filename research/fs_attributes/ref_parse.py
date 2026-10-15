import sys,struct,json
from pathlib import Path
sys.path.insert(0,str(Path('livery-lab/research/fs_parser').resolve()))
from inspect_fs import fields
from probe_paths import scan
D=Path('livery-lab/research/fs_parser/objects.bin').read_bytes()
def records(d):
 n=struct.unpack_from('<H',d)[0];p=2;out=[]
 for i in range(n):
  oid,cls,size=struct.unpack_from('<III',d,p);p+=12
  out.append((oid,cls,d[p:p+size]));p+=size
  assert d[p:p+2]==b'\r\x90';p+=2
 assert p==len(d),(p,len(d))
 return out
for typ in ['AColorItem','ASolidBrushItem','APenItem','ALayerItem']:
 d=Path('livery-lab/research/fs_attributes/'+typ+' Data.bin').read_bytes(); rs=records(fields(d,word=4)[2]);print('\n',typ,len(rs))
 for oid,cls,v in rs:
  f=fields(v,word=4)
  print(oid,cls,{k:(x.hex() if len(x)<14 else 'len '+str(len(x))) for k,x in f.items()})
