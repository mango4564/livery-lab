import sys,struct
from pathlib import Path
sys.path.insert(0,str(Path('livery-lab/research/fs_parser').resolve()))
from inspect_fs import fields
from probe_paths import scan
D=Path('livery-lab/research/fs_parser/objects.bin').read_bytes()
for p in scan(D)[:8]:
 o=p['offset'];f=fields(D[o+12:o+12+p['byte_length']],word=8); b=fields(f[0],word=8)
 print('PATH',o,p['object_id_candidate'],'matrix',p['own_matrix'])
 for k,v in b.items(): print('BASE',k,len(v),v[:180].hex(' '))
