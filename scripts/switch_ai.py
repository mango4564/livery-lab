"""Switch local AI profiles without changing extraction options or exposing keys."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = '彩条提取工作台'
LABELS = {'internal': '社内 Qwen', 'gemini': 'Gemini'}


def read_object(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        raise ValueError(f'无法读取 JSON 配置：{path}') from None
    if not isinstance(value, dict):
        raise ValueError(f'配置必须是 JSON 对象：{path}')
    return value


def load_profile(path, name):
    profile = read_object(path).get(name)
    if not isinstance(profile, dict):
        raise ValueError(f'本地配置缺少 {name}，请参阅 docs/ai-profile-switching.md。')
    for field in ('base_url', 'model', 'api_key'):
        if not isinstance(profile.get(field), str) or not profile[field].strip():
            raise ValueError(f'{LABELS[name]} 的 {field} 尚未配置。')
    try:
        url = urlsplit(profile['base_url'])
        _ = url.port
        valid = (url.scheme in ('http', 'https') and url.hostname and not url.username
                 and not url.password and not url.query and not url.fragment
                 and not url.path.rstrip('/').endswith('/chat/completions'))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f'{LABELS[name]} 的接口根地址格式无效。')
    updates = {k: profile[k].strip() for k in ('base_url', 'model', 'api_key')}
    updates['enabled'] = True
    if 'use_system_proxy' in profile:
        if not isinstance(profile['use_system_proxy'], bool):
            raise ValueError('use_system_proxy 必须为布尔值。')
        updates['use_system_proxy'] = profile['use_system_proxy']
    # Reset profile-specific tuning on switches so Qwen settings cannot bleed
    # into Gemini. Import only local validation, never resolve keys or connect.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from vision import tuning_options, VisionError
    try:
        updates.update(tuning_options(profile))
    except VisionError as exc:
        raise ValueError(str(exc)) from None
    return updates


def running_app_directories(root):
    """Find this repository's running EXEs, without inspecting command lines."""
    if sys.platform != 'win32':
        return []
    command = ("[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
               "@(Get-Process -Name '彩条提取工作台' -ErrorAction SilentlyContinue | "
               "ForEach-Object { $_.Path } | Where-Object { $_ }) | ConvertTo-Json -Compress")
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command],
                                capture_output=True, timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        raw = result.stdout.decode('utf-8-sig', errors='replace').strip()
        if result.returncode or not raw:
            return []
        paths = json.loads(raw)
        paths = [paths] if isinstance(paths, str) else paths
        return [Path(p).resolve().parent for p in paths if isinstance(p, str)
                and Path(p).resolve().is_relative_to(root/'release')]
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def discover_targets(root):
    paths = [root]
    executables = [p for p in (root/'release').glob(f'*/{APP_NAME}/{APP_NAME}.exe')
                   if (p.parent/'build-info.json').is_file()]
    executables += [p for p in (root/'release').glob(f'*/{APP_NAME}.exe')
                    if (p.parent/'build-info.json').is_file()]
    if executables:
        paths.append(max(executables, key=lambda p: p.stat().st_mtime_ns).parent)
    paths.extend(running_app_directories(root))
    return list(dict.fromkeys(p.resolve() for p in paths))


def atomic_write(path, data):
    temp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def switch_profile(root, targets, updates):
    # Validate all targets before changing any. Save exact original bytes so
    # a failed write can restore previous content, including a missing file.
    changes = []
    for directory in dict.fromkeys(Path(p).resolve() for p in targets):
        if not directory.is_dir():
            raise ValueError(f'程序目录不存在：{directory}')
        path = directory/'settings.json'
        original = path.read_bytes() if path.exists() else None
        config = read_object(path) if original is not None else {}
        if all(config.get(k) == value for k, value in updates.items()):
            continue
        config.update(updates)
        data = (json.dumps(config, ensure_ascii=False, indent=2)+'\n').encode('utf8')
        changes.append((path, original, data))
    if not changes:
        return None, []
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]
    backup = root/'.local/backups'/('ai_switch_'+stamp)
    backup.mkdir(parents=True, exist_ok=False)
    manifest = []
    for index, (path, original, _) in enumerate(changes):
        name = f'{index:02d}_settings.json'
        if original is not None:
            (backup/name).write_bytes(original)
        manifest.append({'target': str(path), 'backup': name if original is not None else None})
    (backup/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf8')
    written = []
    try:
        for path, original, data in changes:
            atomic_write(path, data)
            written.append((path, original))
    except OSError:
        failed = False
        for path, original in reversed(written):
            try:
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, original)
            except OSError:
                failed = True
        message = '部分回滚失败，请从备份恢复。' if failed else '已回滚本次已写入的配置。'
        raise ValueError(f'切换写入失败；{message}备份：{backup}') from None
    return backup, [path for path, _, _ in changes]


def main(argv=None):
    parser = argparse.ArgumentParser(description='切换本地社内/Gemini AI 配置，不发起接口请求。')
    parser.add_argument('profile', nargs='?', choices=LABELS)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--app-dir', type=Path, action='append', help='仅切换指定程序目录，可重复指定')
    parser.add_argument('--profiles', type=Path, help='本机私有配置路径')
    parser.add_argument('--dry-run', action='store_true', help='仅显示目标，不写配置')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    name = args.profile
    if name is None:
        print('1. 社内 Qwen\n2. Gemini\n0. 退出')
        try:
            choice = input('选择模型 [1/2/0]：').strip()
        except EOFError:
            return 1
        if choice == '0':
            return 0
        name = {'1': 'internal', '2': 'gemini'}.get(choice)
        if name is None:
            print('无效选择，配置未修改。', file=sys.stderr)
            return 1
    try:
        updates = load_profile(args.profiles or root/'.local/ai-profiles.json', name)
        targets = args.app_dir or discover_targets(root)
        print(f'目标模型：{LABELS[name]}（{updates["model"]}）')
        for directory in targets:
            print(f'程序目录：{directory}')
        if args.dry_run:
            print('仅预览，未写入配置。')
            return 0
        backup, changed = switch_profile(root, targets, updates)
        print(f'已更新 {len(changed)} 份配置。' if changed else '已经是这套配置，无需修改。')
        if backup:
            print(f'原配置备份：{backup}')
        print('请在当前提取完成后切换；下次开始提取时读取新配置。切换本身不调用 AI。')
        return 0
    except (ValueError, OSError) as exc:
        # Never print profile data, server responses, or credentials.
        print(str(exc) if isinstance(exc, ValueError) else '配置文件读写失败，请检查权限。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
