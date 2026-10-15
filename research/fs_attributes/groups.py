import sys,struct
from pathlib import Path
sys.path.insert(0,'livery-lab/research/fs_attributes')
from attributes import *
d=Path('livery-lab/research/fs_parser/objects.bin').read_bytes()
for cls in [10,15,17,18,21,22]:
 count=0
 for i in range(4,len(d)-17):
  if d[i:i+4]!=struct.pack('<I',cls):continue
  n=int.from_bytes(d[i+4:i+12],'little')
  if not 20<n<len(d)-i-12:continue
  try:f=tlv(d[i+12:i+12+n],word=8)
  except:continue
  count+=1
  if count<=2:
   print('CLS',cls,'OFF',i,'OID',int.from_bytes(d[i-4:i],'little'),'LEN',n,{k:len(v) for k,v in f.items()})
   for k,v in f.items(): print('FLD',k,v[:200].hex(' '))
 print('COUNT',cls,count)
