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
        # Smoke-test the review page and its lazy vector thumbnails in the
        # actual frozen runtime, without network or creating product files.
        from review_gallery import ReviewGallery
        root=tk.Tk();root.withdraw()
        sample={'source':'packaged-self-test.fs','source_sha256':'0'*64,
                'objects':[{'id':'a','d':'M0 0 L10 0 L10 4 Z','bbox':[0,0,10,4],'fill':'#167ce2'}],
                'component_categories':{'彩条':['a']}}
        gallery=ReviewGallery(root,sample,Path(sys.executable).parent/'output'/'已复核成品')
        gallery.withdraw();gallery._switch_category('彩条');root.update_idletasks()
        assert len(gallery.photos)==1, 'Review thumbnails unavailable'
        gallery._confirm();assert gallery.choices['彩条']==['a'], 'Review selection unavailable'
        from review_gallery import ObjectSelectionEditor
        gallery._switch_category('logo');gallery._edit_objects()
        editor=next(w for w in gallery.winfo_children() if isinstance(w,ObjectSelectionEditor))
        editor._change({'a'},replace=True);root.update_idletasks()
        assert editor.boxes and editor.draft.vector_image, 'Manual selection preview unavailable'
        editor._apply()
        assert gallery.choices['logo']==['a'], 'Manual category confirmation unavailable'
        gallery.destroy();root.destroy()
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
