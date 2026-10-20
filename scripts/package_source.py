"""Create an allowlisted source demo ZIP, without local settings or binaries."""
from datetime import datetime
import ast
import hashlib
import json
from pathlib import Path
import re
import uuid
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MODULES = (
    'app', 'cli', 'components', 'credentials', 'engine', 'geometry',
    'hover_choice', 'image_preview', 'recognition', 'review_gallery',
    'reviewed_exports', 'runtime', 'source_locator', 'ui', 'vector_preview', 'vision',
)

BOOTSTRAP = r'''@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONPATH="
set "PYTHONHOME="
set "PYTHONNOUSERSITE="
set "BASE_PYTHON="
rem Prefer an existing interpreter with all dependencies already available.
python -X utf8 scripts\source_runtime.py --check >nul 2>&1
if not errorlevel 1 set "BASE_PYTHON=python"
if defined BASE_PYTHON goto ready
for %%V in (3.14 3.13 3.12 3.11 3.10) do call :try_python %%V --check
if defined BASE_PYTHON goto ready
rem No complete environment found: use an existing compatible interpreter.
python -X utf8 scripts\source_runtime.py --probe >nul 2>&1
if not errorlevel 1 set "BASE_PYTHON=python"
if defined BASE_PYTHON goto ready
for %%V in (3.14 3.13 3.12 3.11 3.10) do call :try_python %%V --probe
if not defined BASE_PYTHON goto missing
goto ready
:try_python
if defined BASE_PYTHON exit /b 0
py -%1 -X utf8 scripts\source_runtime.py %2 >nul 2>&1
if not errorlevel 1 set "BASE_PYTHON=py -%1"
exit /b 0
:missing
echo No compatible Python found. Supported: CPython 3.10-3.14 x86-64 with Tcl/Tk.
echo If Python is installed, enable its launcher or add it to PATH, then retry.
pause
exit /b 1
:ready
%BASE_PYTHON% -c "import sys; print('Using Python '+sys.version.split()[0]+' - '+sys.executable)"
'''

INSTALL = BOOTSTRAP + r'''%BASE_PYTHON% -X utf8 scripts\source_runtime.py --install
if errorlevel 1 goto failed
echo.
echo Setup complete. Dependencies are shared across extracted versions.
echo Open the START script to run the app.
pause
exit /b 0
:failed
echo Setup failed. Check the error above and retry.
pause
exit /b 1
'''

START = BOOTSTRAP + r'''%BASE_PYTHON% -X utf8 scripts\source_runtime.py %*
if errorlevel 1 goto failed
exit /b 0
:failed
echo Startup failed. Check the error above or the startup log in this folder.
pause
exit /b 1
'''

GUIDE = '''彩条提取工作台 — 源码体验版

一、准备环境
适用于 Windows 10/11，自动选择电脑上已有的标准 CPython 3.10～3.14 x86-64。
不再要求安装或切换到 Python 3.13；有多个版本时优先复用已装齐依赖的版本。
需包含 Tcl/Tk 和 pip；不支持 Python 3.9 及以下、32 位、ARM64 原生版或无 GIL 版。
已安装 Python 但未找到时，启用 Python launcher 或加入 PATH 后重试。
先把整个 ZIP 解压到有写入权限的普通文件夹，不要在压缩包内直接启动。

二、首次运行
1. 首次使用双击“01_首次安装环境.cmd”。缺少依赖时需要联网，不需要管理员权限。
   依赖由当前 Windows 用户所选的 Python 共享，不在每个解压目录中创建 .venv。
   已安装且版本符合要求时完全跳过下载和安装；看到 Setup complete 即完成。
2. 双击“02_启动程序.cmd”。以后解压新版本也可直接启动，无需重复安装。
   只有依赖缺失或新版本要求的依赖版本变化时，才需要再次运行安装脚本。
   控制台窗口运行期间请保留；关闭程序后窗口自动退出。
3. 添加自己的 FS 文件，选择输出目录。
4. 在“AI 配置”中填写支持图片输入的 Chat Completions 接口地址、模型、密钥，
   支持 HTTP / HTTPS，密钥可直接查看、修改并明文保存到 settings.json。
   并按界面启用 AI。AI 识别需可用接口，可能产生调用费用；
   配置由体验者自行提供，本包没有共享密钥。文字连接测试不等于图片识别成功。
5. 执行提取，对照原稿复核车身、车漆、彩条、文字、logo、门徽。
   可手动点选或框选补充对象，确认六类后导出 SVG 和透明 PNG。
   默认结果写入本文件夹的 output；可在界面更改。

三、包内说明
仅含运行源码、依赖清单、空配置模板、启动脚本及文件 SHA-256 清单。
不含 EXE、Python 运行时、本机配置、密钥、原始素材、历史结果和参考库。
当前识别不依赖参考库；请自行准备可分享的 FS 样例。
本程序支持第 1 页，复杂裁剪、字体等可能不完整，结果需要人工复核。
运行后生成的 settings.json、output 和日志为本机文件；共享依赖保留在 Python 环境中。
旧版目录的 .venv 不会自动删除，本版启动脚本不再使用它。
后续转发请使用原始 ZIP，不要把已经配置和使用过的文件夹整体压缩。

四、排查
安装失败：检查网络及公司软件安装策略，保留窗口中的错误信息。
启动失败：查看窗口信息及程序目录中的“启动错误.log”。
本包不要求关闭杀毒软件，也不保证任何安全产品都不提示。

维护者验证命令（不会主动调用 AI 接口）：
02_启动程序.cmd --self-test
'''


def payload():
    files = {f'{name}.py': (ROOT / f'{name}.py').read_bytes() for name in MODULES}
    files.update({p.relative_to(ROOT).as_posix(): p.read_bytes()
                  for p in sorted((ROOT / 'core').glob('*.py'))})
    for name in ('requirements.txt', 'settings.example.json'):
        files[name] = (ROOT / name).read_bytes()
    files['scripts/source_runtime.py'] = (ROOT / 'scripts/source_runtime.py').read_bytes()
    # Catch missing local modules, including imports inside functions.
    for name, data in files.items():
        if not name.endswith('.py'):
            continue
        tree = ast.parse(data, filename=name)
        compile(tree, name, 'exec')
        for node in ast.walk(tree):
            imports = ([item.name for item in node.names] if isinstance(node, ast.Import)
                       else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            for module in imports:
                candidate = module.split('.')[0] + '.py'
                if (ROOT / candidate).is_file() and candidate not in files:
                    raise RuntimeError(f'Missing local dependency: {candidate}')
        if re.search(rb'(?:sk-[A-Za-z0-9_-]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)', data):
            raise RuntimeError(f'Possible credential in {name}; review before sharing')
    template = json.loads(files['settings.example.json'])
    if template.get('base_url') or template.get('model') or template.get('api_key') or template.get('enabled'):
        raise RuntimeError('Configuration template must be blank and disabled')
    files['01_首次安装环境.cmd'] = INSTALL.replace('\n', '\r\n').encode('ascii')
    files['02_启动程序.cmd'] = START.replace('\n', '\r\n').encode('ascii')
    files['使用说明.txt'] = GUIDE.replace('\n', '\r\n').encode('utf-8-sig')
    files['文件校验.json'] = json.dumps({
        'built_at': datetime.now().astimezone().isoformat(),
        'files_sha256': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }, ensure_ascii=False, indent=2).encode('utf8')
    return files


def main():
    files = payload()
    label = '彩条提取工作台_源码体验版_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
    destination = ROOT / 'release' / (label + '.zip')
    destination.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in files.items():
            bundle.writestr(label + '/' + name, data)
    with zipfile.ZipFile(destination) as bundle:
        assert bundle.testzip() is None, 'ZIP integrity failure'
        assert set(bundle.namelist()) == {label + '/' + name for name in files}
        for name, data in files.items():
            assert bundle.read(label + '/' + name) == data, name
    print(json.dumps({'archive': str(destination), 'file_count': len(files),
                      'bytes': destination.stat().st_size,
                      'sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}, ensure_ascii=False))


if __name__ == '__main__':
    main()
