"""Visual five-category best-pattern review, separate from extraction drafts."""
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

from geometry import svg_document
from image_preview import ImagePreview
from reviewed_exports import LABELS, export_reviewed
from vector_preview import VectorImage


class ReviewGallery(tk.Toplevel):
    PAGE_SIZE = 6

    def __init__(self, parent, result, output_root, manual_candidate=None):
        super().__init__(parent)
        self.result = result
        self.by_id = {o['id']: o for o in result['objects']}
        self.choices, self.choice_labels = {}, {}
        self.category = '车身'
        self.page = 0
        self.current_candidate = None
        self.busy = False
        self.events = queue.Queue()
        self.photos, self.thumbnail_cache = [], {}
        self.candidates = []
        for name in LABELS:
            ids = result.get('component_categories', {}).get(name, [])
            if ids:
                self.candidates.append({'id':'current-'+name, 'label':'当前提取 / 已保存修正',
                                        'selected_ids':list(ids), 'category_hints':[name],
                                        'recommended_categories':[name]})
        self.candidates.extend(result.get('component_candidates', []))
        if not result.get('component_categories'):
            for i, view in enumerate(result.get('views', [])):
                self.candidates.append(dict(view, id=f'legacy-{i}', category_hints=['彩条'],
                                            recommended_categories=[]))
        if manual_candidate and manual_candidate.get('selected_ids'):
            self.candidates.insert(0, manual_candidate)
        self.title('五类图案复核与成品导出 · '+Path(result['source']).stem)
        self.geometry('1380x900')
        self.minsize(1100, 720)
        self.output_root = tk.StringVar(value=str(Path(output_root).resolve()))
        self.show_all = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value='逐类浏览并确认最佳图案；没有图案的类别请明确标记。')
        self._build()
        self._restore()
        self._switch_category()
        self.protocol('WM_DELETE_WINDOW', self._close)
        self._poll_id = None
        if result.get('extraction_mode')=='components' and not result.get('component_candidates'):
            self.status.set('正在从源文件补充旧任务的候选组合，不调用 AI…')
            def load_candidates():
                try:
                    from components import recover_review_candidates
                    self.events.put(('candidates',recover_review_candidates(result)))
                except Exception as exc: self.events.put(('candidate_error',str(exc)))
            threading.Thread(target=load_candidates,daemon=True).start()
        self._poll_id=self.after(100, self._drain)

    def _build(self):
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text=Path(self.result['source']).name, font=('Microsoft YaHei UI', 13, 'bold')).pack(anchor='w')
        ttk.Label(outer, text='① 选择类别   ② 点击缩略图比较、放大查看   ③ 确认最佳或无图案   ④ 导出已确认成品').pack(anchor='w', pady=(4,10))
        split = ttk.Panedwindow(outer, orient='horizontal')
        split.pack(fill='both', expand=True)
        left, middle, right = ttk.Frame(split), ttk.Frame(split), ttk.Frame(split)
        split.add(left, weight=1); split.add(middle, weight=3); split.add(right, weight=5)
        ttk.Label(left, text='类别 / 复核状态').pack(anchor='w')
        self.category_buttons = {}
        for name, label in LABELS.items():
            button = ttk.Button(left, text=label+' · 未确认', command=lambda n=name:self._switch_category(n))
            button.pack(fill='x', pady=6)
            self.category_buttons[name] = button
        ttk.Label(left, text='每类选择一套图案。\n候选可能是相同图案的\n不同侧面、版本或组合。\n\n灰白棋盘格表示透明，\n白色徽章可放大查看。', foreground='#666666').pack(anchor='w', pady=18)
        self.heading = ttk.Label(middle, font=('Microsoft YaHei UI', 12, 'bold'))
        self.heading.pack(anchor='w')
        ttk.Checkbutton(middle, text='显示全部组合（手动归类）', variable=self.show_all,
                        command=lambda:self._switch_category(self.category)).pack(anchor='w', pady=6)
        style = ttk.Style(self)
        style.configure('ReviewGallery.Treeview', rowheight=96)
        self.tree = ttk.Treeview(middle, show='tree', selectmode='browse', style='ReviewGallery.Treeview', height=6)
        self.tree.column('#0', width=330, stretch=True)
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', self._preview)
        self.tree.bind('<Double-1>', lambda _:self._confirm())
        paging = ttk.Frame(middle); paging.pack(fill='x', pady=6)
        ttk.Button(paging, text='上一页', command=lambda:self._change_page(-1)).pack(side='left')
        ttk.Button(paging, text='下一页', command=lambda:self._change_page(1)).pack(side='left', padx=4)
        self.page_label = ttk.Label(paging); self.page_label.pack(side='left', padx=6)
        self.preview = ImagePreview(right)
        self.preview.pack(fill='both', expand=True)
        self.best_label = ttk.Label(right, wraplength=600, foreground='#155e75')
        self.best_label.pack(anchor='w', pady=7)
        self.confirm_button = ttk.Button(right, text='✓ 确认此图为本类最佳', command=self._confirm)
        self.confirm_button.pack(fill='x', pady=4)
        self.absent_button = ttk.Button(right, text='该类没有图案（确认不导出）', command=self._absent)
        self.absent_button.pack(fill='x', pady=4)
        row = ttk.Frame(outer); row.pack(fill='x', pady=(12,4))
        ttk.Label(row, text='成品保存位置').pack(side='left')
        self.output_entry = ttk.Entry(row, textvariable=self.output_root)
        self.output_entry.pack(side='left', fill='x', expand=True, padx=8)
        self.browse_button = ttk.Button(row, text='浏览…', command=self._browse)
        self.browse_button.pack(side='left')
        self.export_button = ttk.Button(row, text='导出五类已确认成品', command=self._export)
        self.export_button.pack(side='left', padx=(8,0))
        ttk.Label(outer, text='成品目录：保存位置 / FS文件名；文件：车身、彩条、文字、logo、徽章 + FS文件名.svg / .png', foreground='#666666').pack(anchor='w')
        ttk.Label(outer, textvariable=self.status, wraplength=1250).pack(anchor='w', pady=6)

    def _restore(self):
        path = Path(self.output_root.get())/Path(self.result['source']).stem/'复核记录.json'
        try:
            saved = json.loads(path.read_text(encoding='utf8'))
            if saved['source_sha256'] != self.result['source_sha256'] or Path(saved['source']).resolve()!=Path(self.result['source']).resolve():
                return
            for name, label in LABELS.items():
                record = saved['categories'][label]
                ids = record['selected_ids']
                if record['status']=='confirmed_absent':
                    self.choices[name] = None
                    self.choice_labels[name] = '无图案'
                elif ids and set(ids)<=set(self.by_id):
                    self.choices[name] = list(ids)
                    self.choice_labels[name] = '上次已复核成品'
                    self.candidates.insert(0, {'id':'saved-'+name, 'label':'上次已复核成品',
                        'selected_ids':list(ids),'category_hints':[name], 'recommended_categories':[]})
        except (OSError, ValueError, KeyError, TypeError):
            return

    def _switch_category(self, name=None):
        self.category = name or self.category
        self.page = 0
        self.current_candidate = None
        self.filtered = []
        seen = set()
        for c in self.candidates:
            ids = c.get('selected_ids', [])
            key = tuple(sorted(ids))
            if not ids or key in seen or not set(ids)<=set(self.by_id):
                continue
            if not self.show_all.get() and self.category not in c.get('category_hints', []):
                continue
            seen.add(key); self.filtered.append(c)
        self.heading.configure(text=LABELS[self.category]+' · 候选图案')
        self._render_page()
        self._refresh_status()

    def _svg(self, candidate):
        chosen = set(candidate['selected_ids'])
        return svg_document([o for o in self.result['objects'] if o['id'] in chosen], width=1400)

    def _thumbnail(self, candidate):
        key = tuple(candidate['selected_ids'])
        if key not in self.thumbnail_cache:
            import re
            svg = self._svg(candidate)
            svg = re.sub(r'(<svg\b[^>]*\bwidth=")[^"]*', r'\g<1>170', svg, count=1)
            svg = re.sub(r'(<svg\b[^>]*\bheight=")[^"]*', r'\g<1>82', svg, count=1)
            im, _, _ = VectorImage(svg).render(1, (0,0), (170,82))
            bg = Image.new('RGBA', im.size, '#eeeeee'); draw = ImageDraw.Draw(bg)
            for y in range(0,82,10):
                for x in range(0,170,10):
                    if (x//10+y//10)%2: draw.rectangle((x,y,x+9,y+9),fill='#bbbbbb')
            bg.alpha_composite(im)
            self.thumbnail_cache[key] = bg
        return ImageTk.PhotoImage(self.thumbnail_cache[key], master=self)

    def _render_page(self):
        self.tree.delete(*self.tree.get_children())
        self.photos = []
        start = self.page*self.PAGE_SIZE
        for index in range(start, min(start+self.PAGE_SIZE, len(self.filtered))):
            c = self.filtered[index]
            photo = self._thumbnail(c); self.photos.append(photo)
            prefix = '推荐 · ' if self.category in c.get('recommended_categories', []) else ''
            self.tree.insert('', 'end', iid=str(index), image=photo,
                             text=' '+prefix+c.get('label',c['id'])+'\n '+str(len(c['selected_ids']))+' 条源路径')
        pages = max(1,(len(self.filtered)+self.PAGE_SIZE-1)//self.PAGE_SIZE)
        self.page_label.configure(text=f'{self.page+1}/{pages} 页 · {len(self.filtered)} 套')
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0]); self._preview()
        else:
            self.preview.set_image(None)
            self.status.set('该类暂无分组候选，可勾选“显示全部组合”，或在工作台补选对象后重新打开。')

    def _change_page(self, delta):
        page = self.page+delta
        if 0 <= page < max(1,(len(self.filtered)+self.PAGE_SIZE-1)//self.PAGE_SIZE):
            self.page=page; self._render_page()

    def _preview(self, _event=None):
        rows = self.tree.selection()
        if not rows:
            return
        self.current_candidate = self.filtered[int(rows[0])]
        vector = VectorImage(self._svg(self.current_candidate))
        self.preview.set_image(None, vector, vector_label='候选 SVG · 可缩放、拖动；点击确认才作为最佳图案')

    def _confirm(self):
        if self.busy or not self.current_candidate:
            return
        self.choices[self.category] = list(self.current_candidate['selected_ids'])
        self.choice_labels[self.category] = self.current_candidate.get('label', self.current_candidate['id'])
        self._refresh_status()

    def _absent(self):
        if self.busy:
            return
        self.choices[self.category] = None
        self.choice_labels[self.category] = '无图案'
        self._refresh_status()

    def _refresh_status(self):
        for name, button in self.category_buttons.items():
            status = '无图案' if name in self.choices and self.choices[name] is None else '已确认' if name in self.choices else '未确认'
            button.configure(text=('▶ ' if name==self.category else '')+LABELS[name]+' · '+status)
        self.best_label.configure(text='本类最佳：'+self.choice_labels.get(self.category,'尚未确认'))
        self.status.set(f'已确认 {len(self.choices)}/5 类。浏览其他候选不会改变已确认图案。')

    def _browse(self):
        folder=filedialog.askdirectory(parent=self, title='选择已复核成品保存位置')
        if folder: self.output_root.set(folder)

    def _export(self):
        if self.busy:
            return
        missing = [label for name,label in LABELS.items() if name not in self.choices]
        if missing:
            messagebox.showinfo('尚未完成复核', '请先确认：'+'、'.join(missing)+'。\n没有图案的类别请选择“该类没有图案”。', parent=self)
            return
        if not self.output_root.get().strip():
            messagebox.showinfo('选择保存位置','请选择成品保存位置。',parent=self); return
        choices = {name:list(ids) if ids is not None else None for name,ids in self.choices.items()}
        output = self.output_root.get()
        self.busy = True
        for widget in (self.export_button,self.confirm_button,self.absent_button,self.browse_button,self.output_entry):
            widget.configure(state='disabled')
        self.status.set('正在生成已确认的 SVG / PNG 成品…')
        def work():
            try: self.events.put(('ok', export_reviewed(self.result, choices, output)))
            except Exception as exc: self.events.put(('error', str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def _drain(self):
        try:
            kind, value = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            if kind in ('candidates','candidate_error'):
                if kind=='candidates':
                    self.candidates.extend(value)
                    self._switch_category(self.category)
                else:
                    self.status.set('旧任务候选补充失败：'+value+'；仍可复核已保存结果。')
                self._poll_id=self.after(100,self._drain)
                return
            self.busy = False
            for widget in (self.export_button,self.confirm_button,self.absent_button,self.browse_button,self.output_entry):
                widget.configure(state='normal')
            if kind=='ok':
                self.status.set('已导出复核成品：'+value['output_dir'])
                messagebox.showinfo('成品已保存',value['output_dir'],parent=self)
            else:
                self.status.set('导出失败：'+value)
                messagebox.showerror('导出失败',value,parent=self)
        if self.winfo_exists(): self._poll_id=self.after(100,self._drain)

    def _close(self):
        if self.busy:
            self.status.set('正在导出成品，请完成后关闭。'); return
        self.destroy()

    def destroy(self):
        poll_id=getattr(self,'_poll_id',None)
        if poll_id is not None:
            try: self.after_cancel(poll_id)
            except tk.TclError: pass
            self._poll_id=None
        super().destroy()
