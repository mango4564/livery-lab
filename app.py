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
        # Exercise the bundled vector renderer too, including native extensions
        # and gradient support; importing empty widgets alone cannot test this.
        from vector_preview import VectorDocument, VectorImage
        vector=VectorImage('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<defs><linearGradient id="g"><stop offset="0" stop-color="white"/>'
            '<stop offset="1" stop-color="blue"/></linearGradient></defs>'
            '<path d="M0 0H100V100H0Z" fill="url(#g)"/></svg>')
        image,_,_=vector.render(1,(0,0),(100,100))
        assert image.getpixel((10,50)) != image.getpixel((90,50)), 'Vector gradients unavailable'
        document=VectorDocument([{'label':'Page 1','image':vector},{'label':'Page 2','image':vector}])
        _,second_y=document.positions[1]
        image,_,_=document.render(1,(-24,-second_y),(100,100))
        assert image.getpixel((10,50)) != image.getpixel((90,50)), 'Second page unavailable'
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
