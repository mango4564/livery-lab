"""Read-only FS reference attribute decoding, independently researched this run.

Supports 32-bit reference streams with counted, sentinel-terminated object lists.
RGB is the stored display RGB, not an ICC physical print color conversion.
"""
from __future__ import annotations
import struct


def tlv(data: bytes, word=4, start=1):
    result={};pos=start;header=word+1
    while pos<len(data):
        if pos+header>len(data): raise ValueError('truncated TLV')
        n=int.from_bytes(data[pos+1:pos+header],'little')
        if n<header or pos+n>len(data): raise ValueError('invalid TLV length')
        key=data[pos]
        if key in result: raise ValueError('duplicate TLV field')
        result[key]=data[pos+header:pos+n];pos+=n
    return result


def reference_records(data):
    raw=tlv(data)[2];count=int.from_bytes(raw[:2],'little');pos=2;out={}
    for _ in range(count):
        oid,cls,size=struct.unpack_from('<III',raw,pos);pos+=12
        if pos+size+2>len(raw): raise ValueError('truncated reference record')
        body=raw[pos:pos+size];pos+=size
        if raw[pos:pos+2]!=b'\r\x90': raise ValueError('missing reference sentinel')
        pos+=2
        if oid in out: raise ValueError('duplicate reference id')
        out[oid]={'class_id':cls,'fields':tlv(body)}
    if pos!=len(raw): raise ValueError('reference trailing bytes')
    return out


def _u32(raw):
    if len(raw)!=4: raise ValueError('expected u32')
    return int.from_bytes(raw,'little')


def decode_references(ole):
    """ole is open olefile.OleFileIO. Return serializable attribute tables."""
    def read(name):
        return reference_records(ole.openstream('FlexiSIGN Contents/Reference/'+name+' Data').read())
    classes=class_names(ole)
    colors={}
    for oid,r in read('AColorItem').items():
        f=r['fields'];cf=f
        # ASpotColorItem derives from the common color item, and keeps tint.
        common=tlv(cf[10]) if classes.get(r['class_id'])=='ARGBColorItem' else tlv(f[0])
        base=tlv(common[0]);name=base.get(2,b'')
        name=name[1:1+name[0]].decode('gb18030',errors='replace') if name else ''
        rgb=cf.get(11) if classes.get(r['class_id'])=='ARGBColorItem' else None
        colors[oid]={'class_id':r['class_id'],'name':name,
            'rgb':list(rgb) if rgb is not None and len(rgb)==3 else None,
            'hex':'#'+rgb.hex() if rgb is not None and len(rgb)==3 else None,
            'spot_tint':f.get(1,b'')[0] if classes.get(r['class_id'])=='ASpotColorItem' and f.get(1) else None}
    brushes={oid:{'class_id':r['class_id'],'color_id':_u32(r['fields'][1])}
             for oid,r in read('ASolidBrushItem').items()}
    pens={}
    for oid,r in read('APenItem').items():
        f=r['fields'];pens[oid]={'class_id':r['class_id'],'color_id':_u32(f[1]),
            'width_candidate':struct.unpack('<d',f[2])[0],
            'raw_fields':{k:v.hex() for k,v in f.items() if k!=0}}
    layers={}
    for oid,r in read('ALayerItem').items():
        f=r['fields'];base=tlv(f[0]);name=base.get(2,b'')
        name=name[1:1+name[0]].decode('gb18030',errors='replace') if name else ''
        layers[oid]={'name':name,'ordinal':_u32(f[1]),'raw_flags':f[2].hex()}
    return {'classes':classes,'colors':colors,'solid_brushes':brushes,'pens':pens,'layers':layers}


def resolve_appearance(raw,refs,word=8):
    """Resolve an object's base[3] ASolidBrush appearance list.

    Returns observed class/ref/color rather than silently inventing unsupported
    gradient, pen, inherited style, visibility or transform semantics.
    """
    if raw in (b'',b'\0\0\0\0'):
        return []
    out=[];pos=0;header=8+word
    while pos<len(raw):
        if raw[pos:pos+2]==b'\r\x90':pos+=2;continue
        if pos+header>len(raw):raise ValueError('truncated appearance record')
        oid,cls=struct.unpack_from('<II',raw,pos)
        size=int.from_bytes(raw[pos+8:pos+header],'little');pos+=header
        f=tlv(raw[pos:pos+size],word=word);pos+=size
        item={'object_id':oid,'class_id':cls}
        if refs.get('classes',{}).get(cls)=='ASolidBrush' or (not refs.get('classes') and cls==9):
            common=tlv(f[0],word=word)
            ref=_u32(common[1]);owner=_u32(common[2]);brush=refs['solid_brushes'].get(ref)
            item.update(brush_id=ref,owner_id=owner)
            if brush:
                color=refs['colors'].get(brush['color_id'])
                item.update(color_id=brush['color_id'],color=color)
            else:item['unresolved']='unknown solid brush reference'
        else:item['unresolved']='unsupported appearance class'
        out.append(item)
    return out


def class_names(ole):
    """Decode the leading ClassTable name section; class IDs are document-local."""
    raw=ole.openstream('FlexiSIGN Contents/Index/ClassTable').read()
    # Four-byte prefix then version byte and 32-bit TLVs. A second independent
    # section follows; stop after name list rather than treating it as one TLV.
    pos=5;parts={}
    for _ in range(3):
        key=raw[pos];n=int.from_bytes(raw[pos+1:pos+5],'little')
        if n<5 or pos+n>len(raw):raise ValueError('invalid class table')
        parts[key]=raw[pos+5:pos+n];pos+=n
    count=int.from_bytes(parts[1],'little');names=[];data=parts[2];pos=0
    for _ in range(count):
        size=data[pos];pos+=1;names.append(data[pos:pos+size].decode('ascii'));pos+=size
    if pos!=len(data):raise ValueError('class name trailing bytes')
    return dict(enumerate(names))


def scan_group_ranges(data,group_class_ids=(17,),word=8):
    """Validated group byte spans and own matrices; NO unverified composition.

    Serialized descendant containment gives ancestry even though transform
    absolute-vs-relative and compound clipping semantics remain unverified.
    """
    groups=[];header=8+word
    for cls in group_class_ids:
        marker=struct.pack('<I',cls);pos=0
        while True:
            p=data.find(marker,pos)
            if p<0:break
            pos=p+1
            if p<4 or p+4+word>=len(data):continue
            size=int.from_bytes(data[p+4:p+4+word],'little');start=p+4+word;end=start+size
            if size<20 or end>len(data):continue
            try:
                f=tlv(data[start:end],word=word)
                container=tlv(f[0],word=word)
                base=tlv(container[0],word=word)
                if len(base[1])!=48 or 1 not in container:continue
                matrix=struct.unpack('<6d',base[1])
                groups.append({'object_id':int.from_bytes(data[p-4:p],'little'),
                  'class_id':cls,'source_offset':p,'payload_offset':start,
                  'end_offset':end,'own_matrix':list(matrix),
                  'appearance_hex':base.get(3,b'').hex(),
                  'transform_status':'own_matrix_observed_composition_unknown',
                  'visibility':'unknown'})
            except (ValueError,KeyError,IndexError,struct.error):continue
    return sorted(groups,key=lambda g:g['source_offset'])


def group_ancestors(record,groups):
    pos=record.get('source_offset',record.get('offset'))
    return [g['object_id'] for g in sorted(groups,key=lambda g:g['end_offset']-g['source_offset'],reverse=True)
            if g['source_offset']<pos<g['end_offset']]


def scan_container_ranges(data,classes,word=8):
    """Observe layer/group/compound ancestry and base matrices without composing.

    Compound nodes require their own boolean/clipping semantics. This table is
    evidence for review, and does not declare descendants independently visible.
    """
    supported={'ALayer','ASubstrateLayer','AGridLayer','AGuideLayer','AGroup',
               'AComposite','ACombineWeld','ACombineCutOut','ACombineCommon'}
    out=[]
    for cls,name in classes.items():
        if name not in supported:continue
        marker=struct.pack('<I',cls);pos=0
        while True:
            p=data.find(marker,pos)
            if p<0:break
            pos=p+1
            if p<4 or p+4+word>=len(data):continue
            size=int.from_bytes(data[p+4:p+4+word],'little');start=p+4+word;end=start+size
            if size<20 or end>len(data):continue
            try:
                f=tlv(data[start:end],word=word);base=None
                for _ in range(6):
                    if len(f.get(1,b''))==48 and 0 in f and 2 in f:
                        base=f;break
                    if 0 not in f:break
                    f=tlv(f[0],word=word)
                if base is None:continue
                matrix=struct.unpack('<6d',base[1])
                out.append({'object_id':int.from_bytes(data[p-4:p],'little'),
                  'class_id':cls,'class_name':name,'source_offset':p,
                  'payload_offset':start,'end_offset':end,'own_matrix':list(matrix),
                  'matrix_is_identity':matrix==(1.,0.,0.,1.,0.,0.),
                  'appearance_hex':base.get(3,b'').hex(),
                  'transform_status':'own_matrix_observed_composition_unknown',
                  'visibility':'unknown',
                  'requires_boolean_semantics':name.startswith('ACombine') or name=='AComposite'})
            except (ValueError,KeyError,IndexError,struct.error):continue
    return sorted(out,key=lambda g:g['source_offset'])


def load_appearances(ole,objects,word=8):
    """Annotate geometry records without modifying inputs.

    Returns (annotated_records, refs). Unknown fills are explicit and never
    replaced with black. An empty appearance list is not proven no-fill.
    """
    refs=decode_references(ole);out=[]
    for original in objects:
        obj=dict(original)
        try:
            appearances=resolve_appearance(bytes.fromhex(obj.get('appearance_hex','')),refs,word)
            obj['appearances']=appearances
            solid=[a for a in appearances if a.get('color',{} ) and a['color'].get('hex')]
            if len(appearances)==1 and len(solid)==1:
                obj.update(fill_status='stored_display_rgb',fill=solid[0]['color']['hex'])
            elif not appearances:obj.update(fill_status='empty_appearance_inheritance_unknown',fill=None)
            else:obj.update(fill_status='unsupported_appearance',fill=None)
        except (ValueError,KeyError,IndexError,struct.error) as exc:
            obj.update(fill_status='invalid_appearance',fill=None,appearance_error=str(exc))
        out.append(obj)
    return out,refs

