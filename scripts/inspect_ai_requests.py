"""Compare sanitized per-task AI request records; never reads API configuration."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_log(path):
    path = Path(path)
    if path.is_dir():
        path = path/'ai_requests.json'
    data = json.loads(path.read_text(encoding='utf8'))
    if not isinstance(data, dict) or not isinstance(data.get('requests'), list):
        raise ValueError('需要新版生成的 ai_requests.json，旧结果不能追溯 HTTP 请求次数。')
    return data


def markdown_report(paths):
    lines = ['# AI 请求对比', '', '批次是模型逻辑请求；HTTP 次数包含传输重试。未返回的 token 用量标为未知。', '']
    for label, path in enumerate(paths, 1):
        data = load_log(path)
        lines.extend([f'## 任务 {label}', '',
            f"逻辑请求 {data['logical_requests']} 批；HTTP {data['http_attempts']} 次；重试 {data['retries']} 次；"
            f"失败 {data['failed_requests']} 批；累计 {data['elapsed_seconds']:g} 秒。", '',
            '| 模型 | 阶段/批次 | 候选数 | 图片尺寸 | HTTP | 秒 | 输入/输出 token | 结果 |',
            '|---|---|---:|---|---:|---:|---|---|'])
        for r in data['requests']:
            sizes = ' + '.join(f"{im.get('width', '?')}×{im.get('height', '?')}" for im in r['images'])
            usage = r.get('usage', {})
            model = str(r['model']).replace('|', '/')
            lines.append(f"| {model} | {r['stage']}/{r['batch']} | {r['candidate_count']} | {sizes} | "
                         f"{len(r['attempts'])} | {r.get('elapsed_seconds', '?')} | "
                         f"{usage.get('prompt_tokens', '?')}/{usage.get('completion_tokens', '?')} | {r['status']} |")
        lines.append('')
        variants = {}
        for r in data['requests']:
            key = json.dumps(r['parameters'], sort_keys=True, ensure_ascii=False)
            variants[key] = variants.get(key, 0)+1
        lines.extend(['实际发送参数（未列出的采样/思考参数沿用服务端默认；接收参数不等于服务端确实执行）：', ''])
        for params, count in variants.items():
            lines.extend([f'{count} 批：', '```json', json.dumps(json.loads(params), ensure_ascii=False, indent=2), '```', ''])
    return '\n'.join(lines)+'\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logs', nargs='+', type=Path, help='任务目录或 ai_requests.json，可一次比较多个任务')
    parser.add_argument('--output', type=Path, help='可选 Markdown 报告位置')
    args = parser.parse_args(argv)
    try:
        report = markdown_report(args.logs)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(report, encoding='utf8')
            print(args.output.resolve())
        else:
            print(report)
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f'无法比较请求记录：{exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
