"""Conservative, colour-independent candidate ranking. Not an AI classifier."""
from __future__ import annotations
import colorsys
import math
from geometry import union_bounds


def rank_candidates(objects):
    bounds=union_bounds(objects)
    doc_w=max(bounds[2]-bounds[0],1e-9);doc_h=max(bounds[3]-bounds[1],1e-9)
    for obj in objects:
        obj['score']=0.0
        box=obj['bbox']; w=box[2]-box[0];h=box[3]-box[1]
        if w<=0 or h<=0:
            continue
        # A separate low-priority geometry pool keeps neutral, small decorative
        # and vertical components available to the model. These scores do not
        # authorise automatic selection.
        relative_area=w*h/(doc_w*doc_h)
        if relative_area>=.00005:
            obj['score']=round(.04+.12*min(relative_area/.01,1)+(.04 if 'C' in obj.get('d','') else 0),4)
        fill=obj.get('fill')
        if not fill or len(fill)!=7:
            continue
        r,g,b=[int(fill[i:i+2],16)/255 for i in (1,3,5)]
        hue,sat,val=colorsys.rgb_to_hsv(r,g,b)
        aspect=w/h
        if aspect>12 and w>doc_w*.075:
            obj['score']=max(obj['score'],.4)
        # Neutral body panels, tyres, windows, small glyphs and tiny markers
        # are excluded from the initial shortlist, but remain selectable in UI.
        if sat<.28 or val<.28 or aspect<2.2 or w<doc_w*.075 or w*h<doc_w*doc_h*.00035:
            continue
        detail=obj.get('details',{})
        if detail.get('closed') is False:
            continue
        score=.23+.18*sat+.17*min(aspect/7,1)+.20*min(w/(doc_w*.65),1)
        if 'C' in obj['d']:
            score+=.12
        if detail.get('closed'):
            score+=.08
        # Unknown compound descendants can still be reviewed, but not mistaken
        # for already evaluated boolean results.
        if obj.get('requires_review_geometry'):
            score-=.08
        obj['score']=round(max(0,min(.98,score)),4)
    return bounds


def candidate_groups(objects):
    candidates=[o for o in objects if o.get('score',0)>=.55]
    # Greedy spatial grouping is capped to avoid quadratic work on text-heavy FS.
    candidates=sorted(candidates,key=lambda o:o['score'],reverse=True)[:500]
    groups=[]
    for obj in candidates:
        box=obj['bbox']; w=box[2]-box[0]; h=box[3]-box[1]
        matches=[]
        for group in groups:
            g=group['bbox'];gw=g[2]-g[0];gh=g[3]-g[1]
            overlap_x=min(g[2],box[2])-max(g[0],box[0])
            overlap_y=min(g[3],box[3])-max(g[1],box[1])
            if overlap_x>min(w,gw)*.12 and overlap_y>=-max(h,gh)*.12:
                matches.append(group)
        if matches:
            g=matches[0];g['objects'].append(obj);g['bbox']=union_bounds(g['objects'])
        else:
            groups.append({'objects':[obj],'bbox':list(box)})
    for group in groups:
        b=group['bbox'];w=b[2]-b[0];h=max(b[3]-b[1],1e-6)
        mean=sum(o['score'] for o in group['objects'])/len(group['objects'])
        group['rank']=round(w*mean*math.sqrt(min(len(group['objects']),4)),2)
        group['selected_ids']=[o['id'] for o in group['objects']]
        group['mean_score']=mean
    # Prefer the upper view for equal mirrored side designs. This is a default
    # presentation choice, not a semantic guarantee; every group is exported.
    groups.sort(key=lambda g:(round(g['rank'],1),round(g['bbox'][3],1)),reverse=True)
    return groups
