"""Launch two source GUI processes with isolated AI settings and export folders."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

if __package__:
    from .switch_ai import ROOT, atomic_write, load_profile, read_object
else:
    from switch_ai import ROOT, atomic_write, load_profile, read_object

LABELS = {'gemini': 'Gemini', 'internal': '社内'}


def prepare_instances(root, profiles, *, dry_run=False):
    # Validate both profiles before writing or launching either app.
    updates = {name: load_profile(profiles, name) for name in LABELS}
    settings = root / 'settings.json'
    base = read_object(settings) if settings.exists() else {}
    now = datetime.now()
    session = now.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8]
    local = root / '.local/tasks' / (now.strftime('%Y%m%d') + '_ai_comparison') / session
    plans = []
    for name, label in LABELS.items():
        directory = local / name
        config = dict(base)
        config.update(updates[name])
        config.setdefault('use_system_proxy', False)
        output = root / 'output/ai-comparison' / session / name
        # Parent-shell overrides must not silently replace a comparison profile.
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith('LIVERY_')}
        env.update(LIVERY_CONFIG_PATH=str(directory / 'settings.json'),
                   LIVERY_WINDOW_TITLE=f'全车矢量提取工作台 · {label}',
                   LIVERY_OUTPUT_DIR=str(output))
        if not dry_run:
            directory.mkdir(parents=True, exist_ok=False)
            atomic_write(directory / 'settings.json',
                         (json.dumps(config, ensure_ascii=False, indent=2) + '\n').encode('utf8'))
        plans.append({'name': name, 'label': label, 'model': config['model'],
                      'env': env, 'log': directory / 'startup.log', 'output': output})
    return plans


def launch_instances(root, plans):
    python = Path(sys.executable)
    windowless = python.with_name('pythonw.exe')
    if sys.platform == 'win32' and windowless.is_file():
        python = windowless
    children = []
    try:
        for plan in plans:
            with plan['log'].open('ab') as log:
                child = subprocess.Popen([str(python), '-X', 'utf8', str(root / 'app.py')],
                                         cwd=root, env=plan['env'], stdout=log, stderr=log,
                                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            children.append(child)
    except OSError:
        # Close only our own processes if the second launch fails.
        for child in children:
            child.terminate()
            child.wait()
        raise
    return children


def main(argv=None):
    parser = argparse.ArgumentParser(description='同时打开 Gemini / 社内 AI 对比窗口，不自动调用 AI。')
    parser.add_argument('--profiles', type=Path, default=ROOT / '.local/ai-profiles.json')
    parser.add_argument('--dry-run', action='store_true', help='检查配置及输出路径，不写文件或打开窗口')
    args = parser.parse_args(argv)
    try:
        plans = prepare_instances(ROOT, args.profiles, dry_run=args.dry_run)
        if not args.dry_run:
            launch_instances(ROOT, plans)
        for plan in plans:
            print(f'{plan["label"]}：{plan["model"]}\n输出：{plan["output"]}')
        print('配置检查通过，未打开窗口。' if args.dry_run else
              '两个窗口已启动。请分别添加同一个 FS 文件并开始提取。')
        return 0
    except (ValueError, OSError) as exc:
        print(str(exc) if isinstance(exc, ValueError) else
              '双开失败，请检查 Python、配置文件权限及本地启动日志。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
