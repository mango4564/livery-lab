"""Review multiple source-view patterns per category before publication."""
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

from geometry import svg_document
from image_preview import ImagePreview
from reviewed_exports import LABELS, SOURCE_VIEWS, export_reviewed
from vector_preview import VectorImage
from source_locator import source_rectangle
import xml.etree.ElementTree as ET


class SelectionPreview(ImagePreview):
    def draw(self):
        super().draw()
        callback = getattr(self, 'draw_selection', None)
        if callback:
            callback()


class ObjectSelectionEditor(tk.Toplevel):
    """Edit source IDs only; a draft has no effect until explicitly confirmed."""
    def __init__(self, parent, objects, selected_ids, category, on_confirm):
        super().__init__(parent)
        self.title('手动指定'+category+' · 原稿对象选择')
        self.geometry('1250x850')
        self.minsize(950, 650)
        self.objects = list(objects)
        self.by_id = {o['id']: o for o in self.objects}
        self.selected = set(selected_ids) & set(self.by_id)
        self.history = []
        self.on_confirm = on_confirm
        self.gesture = None
        self.boxes = {}
        self.mode = tk.StringVar(value='add')
        self.status = tk.StringVar()
        outer = ttk.Frame(self, padding=10); outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='在提取原稿中指定'+category+'：点选最小对象边界；拖出框完整包含要选的对象。').pack(anchor='w')
        ttk.Label(outer, text='重叠对象可在右侧列表多选后添加 / 移除。蓝框表示已选；取消关闭不会修改本类。', foreground='#666666').pack(anchor='w')
        bar = ttk.Frame(outer); bar.pack(fill='x', pady=6)
        for text, mode in [('添加对象', 'add'), ('移除对象', 'remove'), ('平移查看', 'pan')]:
            ttk.Radiobutton(bar, text=text, value=mode, variable=self.mode).pack(side='left', padx=6)
        ttk.Button(bar, text='撤销', command=self._undo).pack(side='left', padx=6)
        ttk.Button(bar, text='清空本次选择', command=lambda: self._change(set(), replace=True)).pack(side='left')
        split = ttk.Panedwindow(outer, orient='horizontal'); split.pack(fill='both', expand=True)
        left, right = ttk.Frame(split), ttk.Frame(split)
        split.add(left, weight=4); split.add(right, weight=2)
        self.viewer = SelectionPreview(left, allow_expand=False)
        self.viewer.pack(fill='both', expand=True)
        self.viewer.draw_selection = self._draw_selection
        canvas = self.viewer.canvas
        canvas.bind('<ButtonPress-1>', self._press)
        canvas.bind('<B1-Motion>', self._motion)
        canvas.bind('<ButtonRelease-1>', self._release)
        canvas.bind('<Double-Button-1>', lambda e: 'break')
        ttk.Label(right, text='所有可导出对象（不受 AI 候选限制）').pack(anchor='w')
        rows = ttk.Frame(right); rows.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(rows, columns=('chosen',), show='tree headings', selectmode='extended')
        self.tree.heading('#0', text='源对象 ID'); self.tree.column('#0', width=165)
        self.tree.heading('chosen', text='已选'); self.tree.column('chosen', width=50, stretch=False)
        scrollbar = ttk.Scrollbar(rows, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y'); self.tree.pack(fill='both', expand=True)
        for obj in self.objects:
            self.tree.insert('', 'end', iid=obj['id'], text=obj['id'], values=('',))
        self.tree.bind('<<TreeviewSelect>>', lambda e: self._show_draft())
        actions = ttk.Frame(right); actions.pack(fill='x')
        ttk.Button(actions, text='添加列表所选', command=lambda: self._change(set(self.tree.selection()))).pack(side='left')
        ttk.Button(actions, text='移除列表所选', command=lambda: self._change(set(self.tree.selection()), remove=True)).pack(side='left', padx=5)
        self.draft = ImagePreview(right, allow_expand=False)
        self.draft.pack(fill='both', expand=True, pady=8)
        ttk.Label(outer, textvariable=self.status).pack(anchor='w', pady=6)
        footer = ttk.Frame(outer); footer.pack(fill='x')
        ttk.Button(footer, text='取消', command=self.destroy).pack(side='right')
        self.apply_button = ttk.Button(footer, text='确认所选对象为'+category, command=self._apply)
        self.apply_button.pack(side='right', padx=8)
        svg = svg_document(self.objects, diagnostic=True)
        self.viewbox = tuple(map(float, ET.fromstring(svg).get('viewBox').split()))
        self.viewer.set_image(None, VectorImage(svg), vector_label='提取原稿矢量 · 浅色对象辅助描边 · 滚轮缩放')
        self._refresh()
        self.transient(parent)
        self.grab_set()
        self.bind('<Escape>', lambda e: self.destroy())

    def _draw_selection(self):
        if not hasattr(self, 'viewbox') or not self.viewer.image:
            return
        v = self.viewer
        size = (v.image.width, v.image.height)
        self.boxes = {}
        for obj in self.objects:
            bounds = obj.get('bbox')
            if not bounds:
                continue
            rectangle = source_rectangle(self.viewbox, bounds, size)
            box = tuple(value * v.scale + v.offset[i % 2] for i, value in enumerate(rectangle))
            self.boxes[obj['id']] = box
            if obj['id'] in self.selected:
                v.canvas.create_rectangle(*box, outline='#0284c7', width=2, tags='selection')
        for oid in self.tree.selection():
            if oid in self.boxes:
                v.canvas.create_rectangle(*self.boxes[oid], outline='#f97316', width=2, dash=(4, 3))

    def _press(self, event):
        if self.mode.get() == 'pan':
            self.viewer._start_drag(event)
        else:
            self.gesture = (event.x, event.y)
        return 'break'

    def _motion(self, event):
        if self.mode.get() == 'pan':
            self.viewer._pan(event)
        elif self.gesture:
            c = self.viewer.canvas
            c.delete('rubberband')
            c.create_rectangle(*self.gesture, event.x, event.y, outline='#f97316', dash=(4, 3), tags='rubberband')
        return 'break'

    def _release(self, event):
        if self.mode.get() == 'pan':
            self.viewer._end_drag(event)
        elif self.gesture:
            x, y = self.gesture
            self.gesture = None
            self.viewer.canvas.delete('rubberband')
            if max(abs(event.x-x), abs(event.y-y)) < 4:
                hits = [oid for oid, (x0, y0, x1, y1) in self.boxes.items()
                        if x0 <= event.x <= x1 and y0 <= event.y <= y1]
                if not hits:
                    hits = [oid for oid, (x0, y0, x1, y1) in self.boxes.items()
                            if x0-3 <= event.x <= x1+3 and y0-3 <= event.y <= y1+3]
                # Small details remain reachable inside a larger body outline.
                hits.sort(key=lambda oid: (self.boxes[oid][2]-self.boxes[oid][0]) *
                                         (self.boxes[oid][3]-self.boxes[oid][1]))
                ids = set(hits[:1])
                if hits:
                    self.tree.selection_set(hits[0]); self.tree.see(hits[0])
            else:
                left, right = sorted((x, event.x)); top, bottom = sorted((y, event.y))
                ids = {oid for oid, (x0, y0, x1, y1) in self.boxes.items()
                       if left <= x0 and x1 <= right and top <= y0 and y1 <= bottom}
            self._change(ids, remove=self.mode.get() == 'remove')
        return 'break'

    def _change(self, ids, *, remove=False, replace=False):
        ids = set(ids) & set(self.by_id)
        changed = ids if replace else self.selected - ids if remove else self.selected | ids
        if changed != self.selected:
            self.history.append(set(self.selected))
            self.selected = changed
        self._refresh()

    def _undo(self):
        if self.history:
            self.selected = self.history.pop()
            self._refresh()

    def _refresh(self):
        for oid in self.by_id:
            self.tree.set(oid, 'chosen', '✓' if oid in self.selected else '')
        self.status.set(f'本类已选 {len(self.selected)} / {len(self.objects)} 个源对象；右下预览为当前选择，橙框为列表定位。')
        self.apply_button.configure(state='normal' if self.selected else 'disabled')
        self.viewer.draw()
        self._show_draft()

    def _show_draft(self):
        self.viewer.draw()
        objects = [o for o in self.objects if o['id'] in self.selected]
        self.draft.set_image(None, VectorImage(svg_document(objects)) if objects else None,
                             vector_label='本类所选对象预览 · 保留源路径')

    def _apply(self):
        ids = [o['id'] for o in self.objects if o['id'] in self.selected]
        if ids:
            self.on_confirm(ids)
            self.destroy()


class ReviewGallery(tk.Toplevel):
    PAGE_SIZE = 6

    def __init__(self, parent, result, output_root, manual_candidate=None):
        super().__init__(parent)
        self.result = result
        self.by_id = {o['id']: o for o in result['objects']}
        self.choices, self.choice_labels = {}, {}
        self.patterns = {name: [] for name in LABELS}
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
        self.title('六类图案复核与成品导出 · '+Path(result['source']).stem)
        self.geometry('1380x900')
        self.minsize(1100, 720)
        self.output_root = tk.StringVar(value=str(Path(output_root).resolve()))
        self.show_all = tk.BooleanVar(value=False)
        self.source_view = tk.StringVar(value=SOURCE_VIEWS[0])
        self.status = tk.StringVar(value='逐类添加需要导出的图案，并标注来源视图；没有图案的类别请明确标记。')
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
        ttk.Label(outer, text='① 选择类别和候选   ② 标注来源视图并添加（每类可添加多个方向）   ③ 六类确认后导出').pack(anchor='w', pady=(4,10))
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
        view_row = ttk.Frame(right); view_row.pack(fill='x')
        ttk.Label(view_row, text='候选来源视图').pack(side='left')
        self.view_combo = ttk.Combobox(view_row, textvariable=self.source_view, values=SOURCE_VIEWS, state='readonly')
        self.view_combo.pack(side='left', padx=8)
        self.confirm_button = ttk.Button(right, text='✓ 添加此图 / 更新此图来源视图', command=self._confirm)
        self.confirm_button.pack(fill='x', pady=4)
        self.pattern_list = tk.Listbox(right, height=4, exportselection=False)
        self.pattern_list.pack(fill='x')
        self.pattern_list.bind('<<ListboxSelect>>', self._preview_pattern)
        self.remove_button = ttk.Button(right, text='移除上方选中的已添加图案', command=self._remove_pattern)
        self.remove_button.pack(fill='x', pady=4)
        self.absent_button = ttk.Button(right, text='该类没有图案（确认不导出）', command=self._absent)
        self.absent_button.pack(fill='x', pady=4)
        self.edit_button = ttk.Button(right, text='识别不正确？在原稿上点选 / 框选本类对象…', command=self._edit_objects)
        self.edit_button.pack(fill='x', pady=8)
        row = ttk.Frame(outer); row.pack(fill='x', pady=(12,4))
        ttk.Label(row, text='成品保存位置').pack(side='left')
        self.output_entry = ttk.Entry(row, textvariable=self.output_root)
        self.output_entry.pack(side='left', fill='x', expand=True, padx=8)
        self.browse_button = ttk.Button(row, text='浏览…', command=self._browse)
        self.browse_button.pack(side='left')
        self.export_button = ttk.Button(row, text='导出六类已确认成品', command=self._export)
        self.export_button.pack(side='left', padx=(8,0))
        ttk.Label(outer, text='每个图案分别导出：类别_来源视图_序号_FS文件名.svg / .png；方向由人工核对，无法判断时保留“待确认视图”。', foreground='#666666').pack(anchor='w')
        ttk.Label(outer, textvariable=self.status, wraplength=1250).pack(anchor='w', pady=6)

    def _restore(self):
        path = Path(self.output_root.get())/Path(self.result['source']).stem/'复核记录.json'
        try:
            saved = json.loads(path.read_text(encoding='utf8'))
            if saved['source_sha256'] != self.result['source_sha256'] or Path(saved['source']).resolve()!=Path(self.result['source']).resolve():
                return
            for name, label in LABELS.items():
                record = saved['categories'].get(label)
                if record is None:
                    continue  # Old five-category reviews keep their confirmed choices.
                ids = record['selected_ids']
                if record['status']=='confirmed_absent':
                    self.choices[name] = None
                    self.choice_labels[name] = '无图案'
                elif ids and set(ids)<=set(self.by_id):
                    entries = record.get('patterns') or [{'selected_ids': list(ids), 'source_view': SOURCE_VIEWS[0]}]
                    if any(not e.get('selected_ids') or not set(e['selected_ids']) <= set(self.by_id)
                           or e.get('source_view') not in SOURCE_VIEWS for e in entries):
                        continue
                    if set(i for e in entries for i in e['selected_ids']) != set(ids):
                        continue
                    self.patterns[name] = [{'selected_ids': list(e['selected_ids']), 'source_view': e['source_view']} for e in entries]
                    self.choices[name] = list(ids)
                    self.choice_labels[name] = '、'.join(e['source_view'] for e in self.patterns[name])
                    for index, entry in enumerate(self.patterns[name]):
                        self.candidates.insert(0, dict(entry, id=f'saved-{name}-{index}', label='上次已复核成品',
                            category_hints=[name], recommended_categories=[]))
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
        saved = next((e for e in self.patterns[self.category]
                      if set(e['selected_ids']) == set(self.current_candidate['selected_ids'])), None)
        self.source_view.set((saved or self.current_candidate).get('source_view', SOURCE_VIEWS[0]))
        vector = VectorImage(self._svg(self.current_candidate))
        self.preview.set_image(None, vector, vector_label='候选 SVG · 可缩放、拖动；选择来源视图后添加')

    def _confirm(self):
        if self.busy or not self.current_candidate:
            return
        self._add_pattern(self.category, self.current_candidate['selected_ids'])
        self._refresh_status()

    def _add_pattern(self, category, ids):
        entry = {'selected_ids': list(ids), 'source_view': self.source_view.get()}
        entries = self.patterns[category]
        for index, old in enumerate(entries):
            if set(old['selected_ids']) == set(ids):
                entries[index] = entry
                break
        else:
            entries.append(entry)
        self._sync_patterns(category)

    def _sync_patterns(self, category):
        entries = self.patterns[category]
        self.choices[category] = list(dict.fromkeys(i for e in entries for i in e['selected_ids'])) if entries else None
        self.choice_labels[category] = '、'.join(e['source_view'] for e in entries) if entries else '无图案'

    def _preview_pattern(self, _event=None):
        rows = self.pattern_list.curselection()
        if not rows or self.busy:
            return
        self.current_candidate = self.patterns[self.category][rows[0]]
        self.source_view.set(self.current_candidate['source_view'])
        self.preview.set_image(None, VectorImage(self._svg(self.current_candidate)),
                               vector_label='已添加图案 · 修改来源视图后点击更新')

    def _remove_pattern(self):
        rows = self.pattern_list.curselection()
        if self.busy or not rows:
            return
        del self.patterns[self.category][rows[0]]
        self._sync_patterns(self.category)
        if not self.patterns[self.category]:
            self.choices.pop(self.category, None)
            self.choice_labels.pop(self.category, None)
        self._refresh_status()

    def _edit_objects(self):
        if self.busy:
            return
        category = self.category
        initial = (self.current_candidate or {}).get('selected_ids', [])
        ObjectSelectionEditor(self, self.result['objects'], initial or [],
                              LABELS[category], lambda ids: self._accept_manual(category, ids))

    def _accept_manual(self, category, ids):
        if self.busy or not ids or not set(ids) <= set(self.by_id):
            return
        candidate = {'id': 'manual-'+category, 'label': '手动指定的'+LABELS[category],
                     'selected_ids': list(ids), 'category_hints': [category],
                     'recommended_categories': []}
        self.candidates = [c for c in self.candidates if c['id'] != candidate['id']]
        self.candidates.insert(0, candidate)
        self._add_pattern(category, ids)
        self._switch_category(category)

    def _absent(self):
        if self.busy:
            return
        self.choices[self.category] = None
        self.patterns[self.category] = []
        self.choice_labels[self.category] = '无图案'
        self._refresh_status()

    def _refresh_status(self):
        for name, button in self.category_buttons.items():
            status = '无图案' if name in self.choices and self.choices[name] is None else '已确认' if name in self.choices else '未确认'
            button.configure(text=('▶ ' if name==self.category else '')+LABELS[name]+' · '+status)
        entries = self.patterns[self.category]
        self.best_label.configure(text=f'本类已添加 {len(entries)} 个图案：'+self.choice_labels.get(self.category,'尚未确认'))
        self.pattern_list.delete(0, 'end')
        for index, entry in enumerate(entries, 1):
            self.pattern_list.insert('end', f"{index:02d} · {entry['source_view']} · {len(entry['selected_ids'])} 条源路径")
        self.status.set(f'已确认 {len(self.choices)}/{len(LABELS)} 类。可跨页添加多个图案；选中已添加项可预览、更新方向或移除。')

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
        patterns = {name: [dict(e, selected_ids=list(e['selected_ids'])) for e in entries]
                    for name, entries in self.patterns.items()}
        output = self.output_root.get()
        self.busy = True
        for widget in (self.export_button,self.confirm_button,self.absent_button,self.edit_button,self.browse_button,self.output_entry):
            widget.configure(state='disabled')
        self.status.set('正在生成已确认的 SVG / PNG 成品…')
        def work():
            try: self.events.put(('ok', export_reviewed(self.result, choices, output, patterns=patterns)))
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
            for widget in (self.export_button,self.confirm_button,self.absent_button,self.edit_button,self.browse_button,self.output_entry):
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
