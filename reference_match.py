"""Match complete, user-labelled motifs to source paths; never export ref paths.

Reference SVGs are read as examples, not executable documents. Normalised path
signatures allow translation, axis scaling and reflection; a common layout
transform must explain every component of a motif before selection is accepted.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

NUMBER=r'[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?'
TOKENS=re.compile(r'[MLCZmlcz]|'+NUMBER)
IDENTITY=(1.,0.,0.,1.,0.,0.)


def _multiply(a,b):
    aa,ab,ac,ad,ae,af=a;ba,bb,bc,bd,be,bf=b
    return (aa*ba+ac*bb,ab*ba+ad*bb,aa*bc+ac*bd,ab*bc+ad*bd,
            aa*be+ac*bf+ae,ab*be+ad*bf+af)


def _transform(text):
    result=IDENTITY
    text=text or ''
    consumed=''
    for match in re.finditer(r'([A-Za-z]+)\s*\(([^)]*)\)',text):
        name=match.group(1);values=[float(n) for n in re.findall(NUMBER,match.group(2))]
        if name=='matrix' and len(values)==6:m=tuple(values)
        elif name=='scale' and len(values) in (1,2):m=(values[0],0,0,values[-1],0,0)
        elif name=='translate' and len(values) in (1,2):m=(1,0,0,1,values[0],values[1] if len(values)==2 else 0)
        elif name=='rotate' and len(values) in (1,3):
            angle=math.radians(values[0]);c,s=math.cos(angle),math.sin(angle);m=(c,s,-s,c,0,0)
            if len(values)==3:
                x,y=values[1:];m=_multiply(_multiply((1,0,0,1,x,y),m),(1,0,0,1,-x,-y))
        else:raise ValueError('Unsupported reference SVG transform')
        result=_multiply(result,m);consumed+=match.group(0)
    if re.sub(r'[\s,]','',re.sub(r'[A-Za-z]+\s*\([^)]*\)','',text)):
        raise ValueError('Invalid reference SVG transform')
    return result


def path_features(d,matrix=IDENTITY):
    tokens=TOKENS.findall(d)
    remainder=TOKENS.sub('',d)
    if re.sub(r'[\s,]','',remainder) or not tokens:
        raise ValueError('Reference matcher supports explicit M/L/C/Z source paths only')
    commands=[];numbers=[];i=0
    sizes={'M':2,'L':2,'C':6,'Z':0}
    while i<len(tokens):
        command=tokens[i];i+=1
        if command not in sizes:
            raise ValueError('Relative/implicit path command unsupported')
        n=sizes[command]
        if i+n>len(tokens):raise ValueError('Truncated path')
        values=[float(v) for v in tokens[i:i+n]];i+=n
        if not all(math.isfinite(v) for v in values):raise ValueError('Nonfinite path')
        commands.append(command)
        a,b,c,e,tx,ty=matrix
        for x,y in zip(values[::2],values[1::2]):
            numbers.extend((a*x+c*y+tx,b*x+e*y+ty))
    if not numbers or commands[0]!='M':raise ValueError('Empty path')
    xs,ys=numbers[::2],numbers[1::2]
    bounds=(min(xs),min(ys),max(xs),max(ys))
    return {'commands':''.join(commands),'numbers':numbers,'bbox':bounds}


def _key(feature,flip=(1,1)):
    x0,y0,x1,y1=feature['bbox'];w=x1-x0;h=y1-y0
    # A line is matched with a stable zero dimension, never an arbitrary divide.
    coords=[]
    for x,y in zip(feature['numbers'][::2],feature['numbers'][1::2]):
        nx=(x-x0)/w if w else 0.;ny=(y-y0)/h if h else 0.
        coords.extend((round(nx if flip[0]>0 else 1-nx,6),round(ny if flip[1]>0 else 1-ny,6)))
    payload=feature['commands']+'|'+','.join(format(v,'.6f') for v in coords)
    return hashlib.sha256(payload.encode('ascii')).hexdigest()


def load_templates(directory):
    if not isinstance(directory,(str,Path)):
        merged=[];errors=[];seen=set()
        for item in directory:
            templates,local_errors=load_templates(item);errors.extend(local_errors)
            for template in templates:
                if template['reference_sha256'] not in seen:
                    seen.add(template['reference_sha256']);merged.append(template)
        return merged,errors
    templates=[];errors=[]
    directory=Path(directory)
    if not directory.is_dir():return templates,errors
    for path in sorted(directory.glob('*.svg')):
        try:
            data=path.read_bytes()
            if len(data)>10*1024*1024:raise ValueError('Reference SVG exceeds 10 MB')
            root=ET.fromstring(data);records=[];source_digest=None
            for node in root.iter():
                if node.tag.split('}')[-1]=='metadata' and node.text:
                    try:
                        digest=json.loads(node.text).get('source_sha256')
                        if isinstance(digest,str) and re.fullmatch('[0-9a-f]{64}',digest):source_digest=digest
                    except (ValueError,AttributeError):pass
            def visit(node,parent):
                matrix=_multiply(parent,_transform(node.get('transform','')))
                tag=node.tag.split('}')[-1]
                if tag in ('use','image','clipPath','mask','filter'):
                    raise ValueError('Reference contains unsupported reuse/raster/clip effects')
                if tag=='path':
                    # SVG display y is down; FS page coordinates are y up.
                    native_matrix=_multiply((1,0,0,-1,0,0),matrix)
                    parts=[part for part in re.split(r'(?=M)',node.get('d','')) if part.strip()]
                    for part_index,part in enumerate(parts):
                        feature=path_features(part,native_matrix)
                        feature['reference_id']=node.get('id','')
                        feature['compound_component']=part_index
                        feature['compound_count']=len(parts)
                        feature['fill']=node.get('fill')
                        records.append(feature)
                for child in node:visit(child,matrix)
            visit(root,IDENTITY)
            if not records:raise ValueError('No supported path components')
            templates.append({'reference_name':path.name,'reference_sha256':hashlib.sha256(data).hexdigest(),
                              'source_sha256':source_digest,'paths':records})
        except (ValueError,ET.ParseError,OverflowError,RecursionError) as exc:
            errors.append({'reference':path.name,'error':str(exc)})
    return templates,errors


def match_motifs(objects, directory, source_sha256=None):
    templates,errors=load_templates(directory)
    directories=[directory] if isinstance(directory,(str,Path)) else list(directory)
    report={'reference_directory':' ; '.join(str(Path(d).resolve()) for d in directories),'template_count':len(templates),
            'errors':errors,'matches':[],'selected_ids':[],'candidate_ids':[]}
    if not templates:return report
    patterns={p['commands'] for t in templates for p in t['paths']}
    index=defaultdict(list)
    for obj in objects:
        d=obj.get('d','')
        if ''.join(re.findall('[MLCZ]',d)) not in patterns:continue
        try:feature=path_features(d)
        except ValueError:continue
        index[_key(feature)].append((obj,feature))
    all_matches=[]
    candidate_ids=set()
    for template in templates:
        paths=template['paths'];complete=[]
        for flip in ((1,1),(-1,1),(1,-1),(-1,-1)):
            possible=[index.get(_key(p,flip),[]) for p in paths]
            for hits in possible:
                candidate_ids.update(obj['id'] for obj,_ in hits)
            if any(not hits for hits in possible):continue
            # Prefer a distinctive component over common rectangles/lines.
            anchor_index=min(range(len(paths)),key=lambda i:(len(possible[i]),-len(paths[i]['numbers'])))
            anchor=paths[anchor_index];r=anchor['bbox'];rw=r[2]-r[0];rh=r[3]-r[1]
            if rw<=0 or rh<=0:continue
            for anchor_obj,anchor_feat in possible[anchor_index][:200]:
                b=anchor_feat['bbox'];sx=(b[2]-b[0])/rw*flip[0];sy=(b[3]-b[1])/rh*flip[1]
                tx=b[0]-sx*(r[0] if sx>0 else r[2]);ty=b[1]-sy*(r[1] if sy>0 else r[3])
                extent=max(max(p['bbox'][2] for p in paths)-min(p['bbox'][0] for p in paths),
                           max(p['bbox'][3] for p in paths)-min(p['bbox'][1] for p in paths),1.)
                tolerance=max(abs(sx),abs(sy))*extent*3e-6+1e-6
                chosen=[];used=set();residual=0.;ambiguous=0;success=True
                for p,hits in zip(paths,possible):
                    q=p['bbox'];xs=[sx*q[0]+tx,sx*q[2]+tx];ys=[sy*q[1]+ty,sy*q[3]+ty]
                    predicted=(min(xs),min(ys),max(xs),max(ys))
                    scored=[]
                    for obj,feature in hits:
                        if obj['id'] in used:continue
                        error=max(abs(x-y) for x,y in zip(feature['bbox'],predicted))
                        if error<=tolerance:
                            colour_penalty=int(bool(p.get('fill') and obj.get('fill') and p['fill'].lower()!=obj['fill'].lower()))
                            scored.append((error,colour_penalty,obj.get('source_offset',0),obj))
                    if not scored:success=False;break
                    scored.sort(key=lambda h:h[:3]);error,_,_,obj=scored[0]
                    ambiguous+=int(len(scored)>1);residual=max(residual,error)
                    used.add(obj['id']);chosen.append(obj['id'])
                if success:
                    compound=defaultdict(list)
                    for p,oid in zip(paths,chosen):
                        if p['compound_count']>1:compound[p['reference_id']].append(oid)
                    identity_distance=abs(sx-1)+abs(sy-1)+(abs(tx)+abs(ty))/extent
                    complete.append({'reference_name':template['reference_name'],
                        'reference_sha256':template['reference_sha256'],'selected_ids':chosen,
                        'matched_count':len(chosen),'required_count':len(paths),
                        'source_digest_matches':bool(source_sha256 and source_sha256==template['source_sha256']),
                        'coordinate_transform':[sx,sy,tx,ty],'layout_residual':residual,
                        'export_groups':[{'reference_id':rid,'source_ids':ids} for rid,ids in compound.items()],
                        'ambiguous_components':ambiguous,'identity_distance':identity_distance,
                        'transfer':'原文件坐标匹配' if source_sha256==template['source_sha256'] else '跨文件图形组合匹配',
                        'match_quality':'normalised_path_signature_and_complete_relative_layout'})
        # A motif may occur on both sides. In a labelled source file prefer its
        # exact annotated coordinate location; for transfers preserve alternatives.
        dedup={}
        for m in complete:
            key=tuple(sorted(m['selected_ids']))
            if key not in dedup or m['identity_distance']<dedup[key]['identity_distance']:dedup[key]=m
        all_matches.extend(dedup.values())
    all_matches.sort(key=lambda m:(not m['source_digest_matches'],m['identity_distance'],m['ambiguous_components']))
    report['matches']=all_matches[:100]
    report['candidate_ids']=sorted(candidate_ids)
    if all_matches:
        best=all_matches[0]
        report.update({k:v for k,v in best.items()})
        report['alternative_count']=len(all_matches)-1
        report['in_sample']=best['source_digest_matches']
    return report
