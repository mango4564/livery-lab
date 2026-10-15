"""GUI launcher and isolated-worker dispatcher, also used by the frozen EXE."""
import sys
from pathlib import Path


def main():
    if len(sys.argv)==7 and sys.argv[1]=='_worker':
        from engine import _run_worker, _write_json
        source,directory=Path(sys.argv[2]),Path(sys.argv[3])
        try:
            _run_worker(source,directory,sys.argv[4],sys.argv[5],sys.argv[6]=='1')
            return 0
        except Exception as exc:
            _write_json(directory/'failure.json',{'source':str(source),'source_sha256':sys.argv[4],
                'error':type(exc).__name__+': '+str(exc)})
            return 2
    if len(sys.argv)==4 and sys.argv[1]=='--extract':
        # Non-interactive packaged smoke test / Windows task integration.
        from engine import process_file
        process_file(Path(sys.argv[2]),Path(sys.argv[3]))
        return 0
    if sys.argv[1:]==['--self-test']:
        # Loads the actual Tcl/Tk and GUI widgets without taking over the desktop.
        import tkinter as tk
        from ui import LiveryApp
        root=tk.Tk();root.withdraw();app=LiveryApp(root)
        root.update_idletasks();root.destroy()
        return 0
    from ui import main as launch
    launch()
    return 0


if __name__=='__main__':
    try:
        code=main()
    except Exception:
        import traceback
        from runtime import APP_DIR
        (APP_DIR/'启动错误.log').write_text(traceback.format_exc(),encoding='utf8')
        if not sys.argv[1:]:
            import tkinter as tk
            from tkinter import messagebox
            root=tk.Tk();root.withdraw()
            messagebox.showerror('启动失败','详情已写入程序目录的“启动错误.log”。')
            root.destroy()
        code=2
    sys.exit(code)
