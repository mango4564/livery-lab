"""Live opt-in AI evaluation without reference labels in model inputs."""
import argparse
from pathlib import Path
import json
import time

from recognition import rank_candidates
from geometry import candidate_image,candidate_sheet
from vision import recognize,read_config,VisionError


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--names',default='3261754,3261627')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent
    acceptance=json.loads((root/'validation/reference_exports/acceptance.json').read_text(encoding='utf8'))
    config=read_config();records=[]
    dest=root/'validation/ai_independent';dest.mkdir(exist_ok=True)
    for item in acceptance['records']:
        if not any(s in Path(item['source']).stem for s in args.names.split(',')):continue
        manifest=Path(item['result_svg']).parent/'manifest.json'
        data=json.loads(manifest.read_text(encoding='utf8'))
        objects=data['objects'];bounds=rank_candidates(objects)
        # Reset every reference-derived score. The model receives no reference
        # pictures, target IDs, labels, source hash or motif matching result.
        pool=sorted(objects,key=lambda o:o['score'],reverse=True)[:min(config['max_candidates'],150)]
        local=dest/Path(item['source']).stem;local.mkdir(exist_ok=True)
        overview=local/'candidates.png';tiles=local/'tiles.png'
        candidate_image(pool,overview,bounds=bounds);candidate_sheet(pool,tiles)
        started=time.monotonic()
        record={'source':item['source'],'input_method':'weak geometric shortlist + original preview + candidate overview + per-object sheet; no reference labels',
                'candidate_ids':[o['id'] for o in pool],'expected_ids':item['selected_ids']}
        print('AI evaluating '+Path(item['source']).name,flush=True)
        try:
            result=recognize({'objects':pool,'candidate_sheet_png':str(tiles)},Path(data['preview_png']),overview,config)
            truth=set(item['selected_ids']);predicted=set(result['selected_ids']);covered=truth&set(record['candidate_ids'])
            record.update(result=result,elapsed_seconds=round(time.monotonic()-started,3),
                          true_positive_ids=sorted(truth&predicted),missed_ids=sorted(truth-predicted),
                          extra_ids=sorted(predicted-truth),pool_truth_coverage=len(covered)/len(truth),
                          exact_id_set=truth==predicted)
            print(json.dumps({'selected_ids':result['selected_ids'],'missed_ids':record['missed_ids'],'extra_ids':record['extra_ids'],
                'confidence':result['confidence'],'seconds':record['elapsed_seconds']},ensure_ascii=False),flush=True)
        except VisionError as exc:
            record.update(error=str(exc),elapsed_seconds=round(time.monotonic()-started,3))
            print('AI error: '+str(exc),flush=True)
        (local/'evaluation.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8');records.append(record)
    (dest/'evaluation_summary.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':main()
