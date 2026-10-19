"""Build and verify a portable Windows release without copying local secrets."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import zipfile


ROOT = Path(__file__).resolve().parents[1]
APP_NAME = '彩条提取工作台'


def run(command, log, *, cwd=ROOT, timeout=600):
    with log.open('a', encoding='utf8') as stream:
        stream.write('\n' + subprocess.list2cmdline([str(arg) for arg in command]) + '\n')
        stream.flush()
        completed = subprocess.run(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                                   timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
    if completed.returncode:
        raise RuntimeError(f'命令失败（退出码 {completed.returncode}），详情见日志：{log}')


def source_digest():
    digest = hashlib.sha256()
    paths = sorted([*ROOT.glob('*.py'), *(ROOT/'core').rglob('*.py'),
                    ROOT/'requirements.txt', ROOT/'settings.example.json', ROOT/'README.md'])
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode('utf8'))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def inherit_local_config(root, app):
    """Keep the latest saved settings and its matching DPAPI file together.

    Called after ZIP creation: the local app inherits configuration while the
    distributable archive stays free of machine-specific configuration.
    """
    candidates = [root/'settings.json']
    candidates.extend((root/'release').glob('*/settings.json'))
    candidates.extend((root/'release').glob('*/彩条提取工作台/settings.json'))
    candidates = [p for p in candidates if p.is_file()]
    if not candidates:
        return False
    source = max(candidates, key=lambda p: p.stat().st_mtime_ns)
    try:
        config = json.loads(source.read_text(encoding='utf-8-sig'))
    except (ValueError, OSError):
        raise RuntimeError(f'最近保存的 AI 配置无法读取，请检查：{source}') from None
    if not isinstance(config, dict):
        raise RuntimeError(f'最近保存的 AI 配置格式无效：{source}')
    if (app/'settings.json').exists() or (app/'.api-key.dpapi').exists():
        raise RuntimeError('目标程序已有本机配置，已停止以保留现有设置。')
    shutil.copy2(source, app/'settings.json')
    key = source.with_name('.api-key.dpapi')
    if key.is_file():
        shutil.copy2(key, app/key.name)
    return True


def main():
    parser = argparse.ArgumentParser(description='自动构建、自检并生成便携 ZIP 包。')
    parser.add_argument('--label', default='自动打包版', help='发布目录前缀，默认：自动打包版')
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('请在 Windows 上运行，生成 Windows EXE。')
    if not args.label.strip() or any(c in args.label for c in '<>:"/\\|?*') or any(ord(c) < 32 for c in args.label):
        parser.error('--label 必须是有效的单个 Windows 文件夹名称。')
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
    release_name = args.label.strip().rstrip('. ') + '_' + stamp
    work = ROOT/'build'/stamp
    log = ROOT/'.local'/'tasks'/(datetime.now().strftime('%Y%m%d')+'_packaging')/stamp/'build.log'
    log.parent.mkdir(parents=True, exist_ok=False)
    work.mkdir(parents=True, exist_ok=False)
    release_root = ROOT/'release'
    release_root.mkdir(exist_ok=True)
    destination = release_root/release_name
    archive = release_root/(release_name+'.zip')
    try:
        print('[1/5] 检查依赖并运行源码自检…', flush=True)
        try:
            run([sys.executable, '-c', 'import PyInstaller, tkinter, PIL, skia, olefile'], log, timeout=30)
        except RuntimeError as exc:
            raise RuntimeError('打包依赖不齐。请使用当前 Python 执行：\n'
                               f'"{sys.executable}" -m pip install -r "{ROOT / "requirements.txt"}" "pyinstaller>=6,<7"\n'
                               f'原始错误见：{log}') from exc
        snapshot = source_digest()
        run([sys.executable, '-X', 'utf8', ROOT/'app.py', '--self-test'], log, timeout=90)
        print('[2/5] 构建 EXE（详细进度写入日志）…', flush=True)
        dist = work/'dist'
        run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--windowed', '--onedir',
             '--name', APP_NAME, '--distpath', dist, '--workpath', work/'work',
             '--specpath', work, '--collect-all', 'skia', '--collect-all', 'PIL',
             '--hidden-import', 'engine', '--hidden-import', 'components',
             '--hidden-import', 'review_gallery', '--hidden-import', 'reviewed_exports', ROOT/'app.py'], log)
        app = dist/APP_NAME
        print('[3/5] 运行打包版 EXE 自检…', flush=True)
        try:
            run([app/(APP_NAME+'.exe'), '--self-test'], log, cwd=app, timeout=90)
        except Exception:
            error = app/'启动错误.log'
            if error.exists():
                with log.open('a', encoding='utf8') as stream:
                    stream.write('\n' + error.read_text(encoding='utf8'))
            raise
        if source_digest() != snapshot:
            raise RuntimeError('打包期间源码或附带文档发生变化，请等待修改结束后重新打包。')
        shutil.copy2(ROOT/'settings.example.json', app/'settings.example.json')
        shutil.copy2(ROOT/'README.md', app/'使用说明.md')
        (app/'build-info.json').write_text(json.dumps({
            'built_at': datetime.now().astimezone().isoformat(),
            'source_sha256': snapshot, 'python': sys.version.split()[0],
            'self_test': 'passed',
        }, ensure_ascii=False, indent=2), encoding='utf8')
        print('[4/5] 生成并检查 ZIP 包…', flush=True)
        staged_zip = Path(shutil.make_archive(str(work/'package'), 'zip', dist))
        with zipfile.ZipFile(staged_zip) as bundle:
            for name in bundle.namelist():
                leaf = Path(name).name.lower()
                if leaf == 'settings.json' or leaf.startswith(('.api-key.dpapi', '.env')):
                    raise RuntimeError('发布包包含本机配置或密钥，已停止发布。')
            if bundle.testzip() is not None:
                raise RuntimeError('ZIP 完整性检查失败，已停止发布。')
        if destination.exists() or archive.exists():
            raise RuntimeError('目标发布路径已经存在，已停止以保留旧包。')
        inherited = inherit_local_config(ROOT, app)
        if inherited:
            print('已沿用本机最近保存的 AI 配置，正在验证配置版启动…', flush=True)
            run([app/(APP_NAME+'.exe'), '--self-test'], log, cwd=app, timeout=90)
        print('[5/5] 保存发布包…', flush=True)
        # Build and verification finish before the launcher can discover the EXE.
        dist.rename(destination)
        staged_zip.rename(archive)
        print(f'\n打包成功。\n程序：{destination / APP_NAME / (APP_NAME+".exe")}\n'
              f'ZIP：{archive}\n日志：{log}\n' +
              ('本机 EXE 已沿用最近保存的 AI 配置。' if inherited else '本机尚无 AI 配置，请在程序内配置。') +
              '\nZIP 不含本机配置或密钥，分享给他人后需自行配置 AI 接口。', flush=True)
        return 0
    except Exception as exc:
        print(f'\n打包失败：{exc}\n日志：{log}\n构建目录保留在：{work}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
