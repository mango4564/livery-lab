import olefile
from pathlib import Path
p=next(Path('全车矢量图素材').glob('*3261754.fs'));o=olefile.OleFileIO(p)
for name in ['Index/ClassTable','Reference/ASolidBrushItem Data','Reference/AColorItem Data','Reference/APenItem Data','Reference/ALayerItem Data']:
 d=o.openstream('FlexiSIGN Contents/'+name).read();print(name,len(d),d[:1800].hex(' '));Path('livery-lab/research/fs_attributes/'+name.split('/')[-1]+'.bin').write_bytes(d)
