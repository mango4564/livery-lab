"""Share Python's installed dependencies across extracted source demo versions."""
import argparse
import importlib
from importlib import metadata
from pathlib import Path
import platform
import site
import subprocess
import sys
import sysconfig


ROOT = Path(__file__).resolve().parents[1]
IMPORTS = {'olefile': 'olefile', 'Pillow': 'PIL', 'PyMuPDF': 'pymupdf', 'skia-python': 'skia'}


def supported_runtime():
    return ((3, 10) <= sys.version_info[:2] <= (3, 14)
            and sys.implementation.name == 'cpython'
            and sys.maxsize > 2**32
            and platform.machine().lower() in ('amd64', 'x86_64')
            and not sysconfig.get_config_var('Py_GIL_DISABLED')
            and sys.prefix == sys.base_prefix)


def check_dependencies(root=ROOT):
    """Check pinned versions and native imports locally, without invoking pip."""
    problems = []
    for line in (root / 'requirements.txt').read_text(encoding='utf-8-sig').splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        name, expected = line.split('==', 1)
        name, expected = name.strip(), expected.strip()
        try:
            actual = metadata.version(name)
        except metadata.PackageNotFoundError:
            problems.append(f'{name}=={expected}: not installed')
            continue
        if actual != expected:
            problems.append(f'{name}=={expected}: installed {actual}')
            continue
        try:
            importlib.import_module(IMPORTS[name])
        except Exception:
            problems.append(f'{name}: import failed; reinstall this package')
    return problems


def install_dependencies(root=ROOT):
    problems = check_dependencies(root)
    if not problems:
        print('Dependencies already installed. No download or installation needed.', flush=True)
        return 0
    print('\n'.join(problems), flush=True)
    if not site.ENABLE_USER_SITE:
        print('Python user-site is disabled. Enable it before installing shared dependencies.')
        return 1
    print(f'Installing dependencies for this Windows user and Python {sys.version_info.major}.{sys.version_info.minor}...', flush=True)
    result = subprocess.run([sys.executable, '-m', 'pip', 'install', '--user',
                             '--disable-pip-version-check', '--no-warn-script-location',
                             '-r', str(root / 'requirements.txt')])
    if result.returncode:
        return result.returncode
    # Reload in a fresh interpreter: newly installed versions must not reuse
    # native modules already imported during the initial dependency check.
    return subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--check'],
                          cwd=root).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--install', action='store_true')
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--self-test', action='store_true')
    modes.add_argument('--probe', action='store_true')
    args = parser.parse_args()
    if not supported_runtime():
        print('Use an existing CPython 3.10-3.14 x86-64 interpreter with the standard GIL, outside a virtual environment.')
        return 1
    try:
        import tkinter
        import tkinter.ttk
    except ImportError:
        print('Python Tcl/Tk support is missing. Install Python with Tcl/Tk enabled.')
        return 1
    if args.probe:
        return 0
    if args.install:
        return install_dependencies()
    problems = check_dependencies()
    if problems:
        print('\n'.join(problems))
        print('Run the INSTALL script once to install or update shared dependencies.')
        return 1
    if args.check:
        print('Shared dependencies verified. No network access needed.')
        return 0
    command = [sys.executable, '-X', 'utf8', str(ROOT / 'app.py')]
    if args.self_test:
        command.append('--self-test')
    return subprocess.run(command, cwd=ROOT).returncode


if __name__ == '__main__':
    sys.exit(main())
