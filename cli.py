"""Command-line entry point for reproducible per-file and batch runs."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from engine import process_file, export_selection


def main():
    parser=argparse.ArgumentParser(description='独立 FS 彩条提取（原始曲线＋待复核候选）')
    parser.add_argument('source',type=Path,help='FS 文件或素材文件夹')
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parent/'output')
    parser.add_argument('--ai',action='store_true',help='使用本机 settings.json 和密钥环境变量配置的视觉接口')
    parser.add_argument('--force',action='store_true',help='兼容参数；每次提取都会创建新的处理目录')
    parser.add_argument('--components',action='store_true',help='识别车身、彩条、文字、logo、门徽，需同时启用 --ai')
    parser.add_argument('--reference-directory',type=Path,help='旧版兼容参数；当前独立识别不读取或匹配参考库')
    parser.add_argument('--ids',help='单文件手动确认的对象 ID，以逗号分隔')
    parser.add_argument('-j','--jobs',type=int,default=os.cpu_count() or 4,help='并发处理任务数（默认使用 CPU 逻辑核心数，AI 模式同样生效）')
    args=parser.parse_args()
    if args.source.is_dir():
        sources=sorted(p for p in args.source.rglob('*') if p.is_file() and p.suffix.lower()=='.fs')
    else:
        sources=[args.source]
    if not sources:
        parser.error('没有找到 FS 文件')
    if args.ids and len(sources)!=1:
        parser.error('--ids 仅适用于单文件')
    args.output.mkdir(parents=True,exist_ok=True)
    if args.jobs > 1 and len(sources) > 1 and not args.ids:
        worker_count = max(1, min(args.jobs, len(sources)))
        print(f'启动并发处理（{worker_count} 个任务）...', flush=True)
        report_map = {}
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            def _run_single(n, source):
                print(f'[{n}/{len(sources)}] 开始：{source.name}', flush=True)
                try:
                    result = process_file(
                        source,
                        args.output,
                        {'use_ai': args.ai, 'force': args.force, 'extraction_mode': 'components' if args.components else 'stripes', 'reference_directory': str(args.reference_directory) if args.reference_directory else ''},
                        progress=lambda s: print(f'  [{source.name}] {s}', flush=True)
                    )
                    row = {k: result[k] for k in ('source', 'status', 'output_dir', 'selected_ids', 'method', 'elapsed_seconds')}
                    row['object_count'] = len(result['objects'])
                    row['candidate_count'] = result['candidate_count']
                    print(f'  [{source.name}] 完成：{result["result_svg"]}', flush=True)
                    return n, row
                except Exception as exc:
                    row = {'source': str(source.resolve()), 'status': 'failed', 'error': str(exc)}
                    print(f'  [{source.name}] 失败：{exc}', flush=True)
                    return n, row
            futures = [executor.submit(_run_single, n, s) for n, s in enumerate(sources, 1)]
            for fut in as_completed(futures):
                idx, row = fut.result()
                report_map[idx] = row
        report = [report_map[i] for i in sorted(report_map.keys())]
    else:
        report=[]
        for n,source in enumerate(sources,1):
            print(f'[{n}/{len(sources)}] {source.name}',flush=True)
            try:
                result=process_file(source,args.output,{'use_ai':args.ai,'force':args.force,
                    'extraction_mode':'components' if args.components else 'stripes',
                    'reference_directory':str(args.reference_directory) if args.reference_directory else ''},progress=lambda s:print('  '+s,flush=True))
                if args.ids:
                    result=export_selection(result,[s.strip() for s in args.ids.split(',') if s.strip()])
                row={k:result[k] for k in ('source','status','output_dir','selected_ids','method','elapsed_seconds')}
                row['object_count']=len(result['objects']);row['candidate_count']=result['candidate_count']
                print('  '+result['result_svg'],flush=True)
            except Exception as exc:
                row={'source':str(source.resolve()),'status':'failed','error':str(exc)}
                print('  失败：'+str(exc),flush=True)
            report.append(row)
    path=args.output/('batch_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.json')
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print('批次记录：'+str(path),flush=True)
    return int(any(r['status']=='failed' for r in report))


if __name__=='__main__':
    sys.exit(main())
