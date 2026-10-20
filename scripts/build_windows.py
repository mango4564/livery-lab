"""Build and verify a portable Windows release without copying local secrets."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import zipfile

if __package__:
    from .package_source import create_archive
else:
    from package_source import create_archive


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


def source_digest(root=ROOT):
    digest = hashlib.sha256()
    paths = sorted([*root.glob('*.py'), *(root/'core').rglob('*.py'),
                    root/'requirements.txt', root/'settings.example.json', root/'README.md',
                    *(root/'scripts'/name for name in
                      ('build_windows.py', 'package_source.py', 'source_runtime.py'))])
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode('utf8'))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def matching_release(root, fingerprint):
    """Only use a completed, self-tested release matching the current sources."""
    candidates = []
    for directory in (root/'release').glob('*'):
        if not directory.is_dir():
            continue
        for app in (directory, directory/APP_NAME):
            executable = app/(APP_NAME+'.exe')
            if not executable.is_file() or not (app/'_internal').is_dir():
                continue
            try:
                info = json.loads((app/'build-info.json').read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                continue
            if isinstance(info, dict) and info.get('source_sha256') == fingerprint and info.get('self_test') == 'passed':
                candidates.append(executable)
    return max(candidates, key=lambda p: p.stat().st_mtime_ns, default=None)


@contextmanager
def launch_build_lock(root, timeout=900):
    """Windows releases share one OS lock; a crash automatically releases it."""
    import msvcrt

    directory = root/'build'
    directory.mkdir(exist_ok=True)
    with (directory/'launch-latest.lock').open('a+b') as lock:
        lock.seek(0, 2)
        if not lock.tell():
            lock.write(b'0')
            lock.flush()
        deadline = time.monotonic() + timeout
        while True:
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('等待自动打包超时，请稍后重试。') from None
                time.sleep(0.25)
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def ensure_latest(root=ROOT):
    with launch_build_lock(root):
        latest = matching_release(root, source_digest(root))
        if latest is None:
            # Keep stdout exclusively for the path consumed by PowerShell.
            with redirect_stdout(sys.stderr):
                if main(['--backup-git']) != 0:
                    raise RuntimeError('自动打包失败，未启动旧版本。请查看启动日志中的详细原因。')
            latest = matching_release(root, source_digest(root))
            if latest is None:
                raise RuntimeError('打包后源码又发生变化或发布包不完整，请稍后重试。')
        return latest


def backup_git(root, log):
    """Commit explicit nonignored paths, leaving excluded staged files alone."""
    def git(*args, data=None, allowed=(0,)):
        result = subprocess.run(
            ['git', '-C', str(root), *args], input=data, capture_output=True,
            timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        with log.open('ab') as stream:
            stream.write(result.stdout + result.stderr)
        if result.returncode not in allowed:
            raise RuntimeError(f'Git 自动备份失败，已停止打包。请查看日志：{log}')
        return result.stdout

    top = Path(git('rev-parse', '--show-toplevel').decode('utf8').strip()).resolve()
    if top != root.resolve():
        raise RuntimeError('Git 仓库根目录与工作台目录不一致，已停止自动备份。')
    records = iter(git('status', '--porcelain=v1', '-z', '--untracked-files=all').split(b'\0'))
    paths = set()
    for record in records:
        if not record:
            continue
        state, path = record[:2], record[3:]
        if b'U' in state or state in (b'AA', b'DD'):
            raise RuntimeError('Git 存在未解决的合并冲突，已停止自动打包。')
        paths.add(path)
        if b'R' in state or b'C' in state:
            paths.add(next(records))
    if paths:
        pathspec = b'\0'.join(sorted(paths)) + b'\0'
        ignored = set(git('check-ignore', '--no-index', '-z', '--stdin',
                          data=pathspec, allowed=(0, 1)).split(b'\0'))
        paths -= ignored
    if paths:
        pathspec = b'\0'.join(sorted(paths)) + b'\0'
        indexed = set(git('ls-files', '-z').split(b'\0'))
        # A staged rename/deletion has already removed its old path from index.
        to_add = {path for path in paths if path in indexed or (root / path.decode('utf8')).exists()}
        if to_add:
            git('--literal-pathspecs', 'add', '--pathspec-from-file=-', '--pathspec-file-nul',
                data=b'\0'.join(sorted(to_add)) + b'\0')
        message = 'backup: before automatic build ' + datetime.now().astimezone().isoformat(timespec='seconds')
        git('--literal-pathspecs', 'commit', '--only', '-m', message,
            '--pathspec-from-file=-', '--pathspec-file-nul', data=pathspec)
    commit = git('rev-parse', 'HEAD').decode('ascii').strip()
    print(('已自动提交 Git 备份：' if paths else 'Git 无新改动，沿用备份：') + commit, flush=True)
    return commit


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


def main(argv=None):
    parser = argparse.ArgumentParser(description='自动构建、自检并生成便携 ZIP 包。')
    parser.add_argument('--label', default='自动打包版', help='发布目录前缀，默认：自动打包版')
    parser.add_argument('--ensure-latest', action='store_true', help='必要时自动打包，并输出与当前源码一致的 EXE 路径')
    parser.add_argument('--backup-git', action='store_true', help='打包前自动提交 Git 本地备份；桌面自动打包会启用')
    args = parser.parse_args(argv)
    if sys.platform != 'win32':
        parser.error('请在 Windows 上运行，生成 Windows EXE。')
    if args.ensure_latest:
        try:
            print(ensure_latest())
            return 0
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 1
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
    demo_name = APP_NAME+'_源码体验版_'+stamp+'.zip'
    demo_archive = release_root/demo_name
    try:
        git_commit = backup_git(ROOT, log) if args.backup_git else None
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
            'git_commit': git_commit,
        }, ensure_ascii=False, indent=2), encoding='utf8')
        print('[4/5] 生成并检查 EXE ZIP 和同事源码体验 ZIP…', flush=True)
        staged_zip = Path(shutil.make_archive(str(work/'package'), 'zip', dist))
        with zipfile.ZipFile(staged_zip) as bundle:
            for name in bundle.namelist():
                leaf = Path(name).name.lower()
                if leaf == 'settings.json' or leaf.startswith(('.api-key.dpapi', '.env')):
                    raise RuntimeError('发布包包含本机配置或密钥，已停止发布。')
            if bundle.testzip() is not None:
                raise RuntimeError('ZIP 完整性检查失败，已停止发布。')
        staged_demo = work/demo_name
        create_archive(staged_demo, root=ROOT)
        if source_digest() != snapshot:
            raise RuntimeError('生成体验包期间源码发生变化，请等待修改结束后重新打包。')
        if destination.exists() or archive.exists() or demo_archive.exists():
            raise RuntimeError('目标发布路径已经存在，已停止以保留旧包。')
        inherited = inherit_local_config(ROOT, app)
        if inherited:
            print('已沿用本机最近保存的 AI 配置，正在验证配置版启动…', flush=True)
            run([app/(APP_NAME+'.exe'), '--self-test'], log, cwd=app, timeout=90)
        print('[5/5] 保存发布包…', flush=True)
        # Build and verification finish before the launcher can discover the EXE.
        dist.rename(destination)
        staged_zip.rename(archive)
        staged_demo.rename(demo_archive)
        print(f'\n打包成功。\n程序：{destination / APP_NAME / (APP_NAME+".exe")}\n'
              f'EXE ZIP：{archive}\n同事体验 ZIP：{demo_archive}\n日志：{log}\n' +
              ('本机 EXE 已沿用最近保存的 AI 配置。' if inherited else '本机尚无 AI 配置，请在程序内配置。') +
              '\n发给同事请选“源码体验版”ZIP，解压后按说明安装环境并启动。'
              '\n两个 ZIP 均不含本机配置或密钥，分享给他人后需自行配置 AI 接口。', flush=True)
        return 0
    except Exception as exc:
        print(f'\n打包失败：{exc}\n日志：{log}\n构建目录保留在：{work}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
