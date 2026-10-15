exec(open('livery-lab/research/fs_attributes/probe.py',encoding='utf-8-sig').read().split('for p in scan(D)[:8]:')[0])
from collections import Counter,defaultdict
ct=Counter(); examples={}
for p in scan(D):
 o=p['offset'];f=fields(D[o+12:o+12+p['byte_length']],word=8);b=fields(f[0],word=8)
 ct[tuple((k,len(v)) for k,v in b.items())]+=1
 if 3 in b:
  v=b[3]; oid,cl,ln=struct.unpack_from('<IIQ',v);af=fields(v[16:16+ln],word=8)
  ct[('appearance',cl)]+=1;examples.setdefault(cl,af)
print(ct)
for cl,af in examples.items():print(cl,{k:v.hex() for k,v in af.items()})
