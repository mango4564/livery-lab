"""Review multiple source-view patterns per category before publication."""
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

from geometry import svg_document, union_bounds
from image_preview import ImagePreview
from reviewed_exports import LABELS, SOURCE_VIEWS, export_reviewed, infer_source_view
from vector_preview import VectorImage
from source_locator import source_rectangle, SourceLocator, SourceLocatorRenderer
from core.source_regions import object_regions, region_for_ids, split_by_region, valid_region_name
import xml.etree.ElementTree as ET


def classified_patterns(category, ids, candidates):
    """Keep independently classified marks separate, even within one region."""
    if category not in ('logo', '门徽'):
        return [list(ids)]
    selected = set(ids)
    groups = []
    for c in candidates:
        members = set(c.get('selected_ids', []))
        if (category in c.get('recommended_categories', []) and members
                and members <= selected and members not in groups):
            groups.append(members)
    groups = [g for g in groups if not any(g < other for other in groups)]
    covered = set().union(*groups) if groups else set()
    if selected-covered:
        groups.append(selected-covered)
    return [[oid for oid in ids if oid in group] for group in groups]


class SelectionPreview(ImagePreview):
    def draw(self):
        super().draw()
        callback = getattr(self, 'draw_selection', None)
        if callback:
            callback()


class ObjectSelectionEditor(tk.Toplevel):
    """Edit source IDs only; a draft has no effect until explicitly confirmed."""
    def __init__(self, parent, objects, selected_ids, category, on_confirm, *, on_category_confirm=None):
        super().__init__(parent)
        self.title('手动指定'+category+' · 原稿对象选择')
        self.geometry('1250x850')
        self.minsize(950, 650)
        self.objects = list(objects)
        self.by_id = {o['id']: o for o in self.objects}
        self.selected = set(selected_ids) & set(self.by_id)
        self.history = []
        self.on_confirm = on_confirm
        self.on_category_confirm = on_category_confirm
        self.category = tk.StringVar(value=category)
        self.gesture = None
        self.boxes = {}
        self.mode = tk.StringVar(value='pan')
        self.status = tk.StringVar()
        outer = ttk.Frame(self, padding=10); outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='在整张提取原稿中选取图案：默认平移查看；切换“添加对象”后点选或框选，不受 AI 候选限制。').pack(anchor='w')
        ttk.Label(outer, text='“取消选取”可点选 / 框选已选对象；重叠对象也可在右侧列表取消。蓝框表示已选；取消关闭不会修改本类。', foreground='#666666').pack(anchor='w')
        bar = ttk.Frame(outer); bar.pack(fill='x', pady=6)
        for text, mode in [('平移查看', 'pan'), ('添加对象', 'add'), ('取消选取', 'remove')]:
            ttk.Radiobutton(bar, text=text, value=mode, variable=self.mode).pack(side='left', padx=6)
        ttk.Button(bar, text='撤销', command=self._undo).pack(side='left', padx=6)
        ttk.Button(bar, text='清空并重新选取', command=self._start_over).pack(side='left')
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
        ttk.Button(actions, text='取消列表所选', command=lambda: self._change(set(self.tree.selection()), remove=True)).pack(side='left', padx=5)
        self.draft = ImagePreview(right, allow_expand=False)
        self.draft.pack(fill='both', expand=True, pady=8)
        ttk.Label(outer, textvariable=self.status).pack(anchor='w', pady=6)
        footer = ttk.Frame(outer); footer.pack(fill='x')
        if self.on_category_confirm:
            ttk.Label(footer, text='将所选对象作为：').pack(side='left')
            self.category_combo = ttk.Combobox(footer, textvariable=self.category,
                                               values=list(LABELS.values()), state='readonly', width=12)
            self.category_combo.pack(side='left', padx=6)
            ttk.Label(footer, text='请选择图案类别后确认').pack(side='left')
        ttk.Button(footer, text='取消', command=self.destroy).pack(side='right')
        self.apply_button = ttk.Button(footer, text='确认所选对象为'+category, command=self._apply)
        self.apply_button.pack(side='right', padx=8)
        self.category.trace_add('write', lambda *_: self._update_category())
        self._update_category()
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
        v.canvas.delete('selection')
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
                v.canvas.create_rectangle(*self.boxes[oid], outline='#f97316', width=2, dash=(4, 3), tags='selection')

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
                # Ignore unselected details when cancelling an overlapping selection.
                boxes = {oid: box for oid, box in self.boxes.items()
                         if self.mode.get() != 'remove' or oid in self.selected}
                hits = [oid for oid, (x0, y0, x1, y1) in boxes.items()
                        if x0 <= event.x <= x1 and y0 <= event.y <= y1]
                if not hits:
                    hits = [oid for oid, (x0, y0, x1, y1) in boxes.items()
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

    def _start_over(self):
        self.tree.selection_remove(*self.tree.selection())
        self._change(set(), replace=True)
        self.mode.set('add')

    def _undo(self):
        if self.history:
            self.selected = self.history.pop()
            self._refresh()

    def _refresh(self):
        for oid in self.by_id:
            self.tree.set(oid, 'chosen', '✓' if oid in self.selected else '')
        self.status.set(f'本类已选 {len(self.selected)} / {len(self.objects)} 个源对象；右下预览为当前选择，橙框为列表定位。')
        self._update_category()
        self.viewer.draw()
        self._show_draft()

    def _show_draft(self):
        self.viewer.draw()
        objects = [o for o in self.objects if o['id'] in self.selected]
        self.draft.set_image(None, VectorImage(svg_document(objects)) if objects else None,
                             vector_label='本类所选对象预览 · 保留源路径')

    def _update_category(self):
        category = self.category.get()
        valid = not self.on_category_confirm or category in LABELS.values()
        self.title('手动选取图案'+(' · '+category if category else ' · 请选择图案类别'))
        self.apply_button.configure(text='确认所选对象为'+category if category else '请选择图案类别',
                                    state='normal' if self.selected and valid else 'disabled')

    def _apply(self):
        ids = [o['id'] for o in self.objects if o['id'] in self.selected]
        if ids:
            if self.on_category_confirm:
                category = next((key for key, label in LABELS.items() if label == self.category.get()), None)
                if category is None:
                    return
                self.on_category_confirm(category, ids)
            else:
                self.on_confirm(ids)
            self.destroy()


class ReviewGallery(tk.Toplevel):
    PAGE_SIZE = 6

    def __init__(self, parent, result, output_root, manual_candidate=None):
        super().__init__(parent)
        self.result = dict(result, source_regions=[dict(r) for r in result.get('source_regions', [])])
        self.regions = self.result['source_regions']
        stripe_ids = set(result.get('component_categories', {}).get('彩条') or [])
        for c in result.get('component_candidates', []) + ([manual_candidate] if manual_candidate else []):
            if '彩条' in c.get('category_hints', []):
                stripe_ids.update(c.get('selected_ids', []))
        self.region_assignments = object_regions(result['objects'], self.regions, partial_ids=stripe_ids)
        self.by_id = {o['id']: o for o in result['objects']}
        self.choices, self.choice_labels = {}, {}
        self.patterns = {name: [] for name in LABELS}
        self.category = '车身'
        self.page = 0
        self.current_candidate = None
        self.busy = False
        self.events = queue.Queue()
        self._locator_requests = queue.Queue(maxsize=1)
        self._locator_stop = threading.Event()
        self._locator_generation = 0
        self.photos, self.thumbnail_cache = [], {}
        self.candidates = []
        for name in LABELS:
            ids = result.get('component_categories', {}).get(name, [])
            if ids:
                groups = classified_patterns(name, ids, result.get('component_candidates', []))
                for index, group in enumerate(groups):
                    self.candidates.append({'id':f'current-{name}-{index}', 'label':'当前提取 / 已保存修正',
                                            'selected_ids':group, 'category_hints':[name],
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
        self._split_region_candidates()
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
        threading.Thread(target=self._locator_worker,
                         args=(result, self._locator_requests, self.events, self._locator_stop),
                         daemon=True).start()

    def _build(self):
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text=Path(self.result['source']).name, font=('Microsoft YaHei UI', 13, 'bold')).pack(anchor='w')
        ttk.Label(outer, text='① 核对 AI 原稿区域   ② 选择候选并添加（自动继承区域名）   ③ 六类确认后导出').pack(anchor='w', pady=(4,10))
        self.region_button = ttk.Button(outer, text='查看原稿区域 / 统一修改区域名称…', command=self._edit_regions)
        self.region_button.pack(anchor='w', pady=(0, 5))
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
        ttk.Label(middle, text='默认展示已分类图案；补选可显示全部组合或从原稿选取。',
                  foreground='#666666').pack(anchor='w', pady=(0, 4))
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
        self.source_locator = SourceLocator(self.preview.canvas, title='原稿位置 · 正在加载…')
        self.source_locator.place(x=8, y=8)
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
        self.new_pattern_button = ttk.Button(right, text='从零自由选取新图案（不使用当前候选）…',
                                             command=lambda: self._edit_objects(from_scratch=True))
        self.new_pattern_button.pack(fill='x', pady=(0, 8))
        row = ttk.Frame(outer); row.pack(fill='x', pady=(12,4))
        ttk.Label(row, text='成品保存位置').pack(side='left')
        self.output_entry = ttk.Entry(row, textvariable=self.output_root)
        self.output_entry.pack(side='left', fill='x', expand=True, padx=8)
        self.browse_button = ttk.Button(row, text='浏览…', command=self._browse)
        self.browse_button.pack(side='left')
        self.export_button = ttk.Button(row, text='导出六类已确认成品', command=self._export)
        self.export_button.pack(side='left', padx=(8,0))
        ttk.Label(outer, text='每个图案分别导出：区域名称_类别_序号_FS文件名.svg / .png；区域名称自动继承，来源方向可手动更正。', foreground='#666666').pack(anchor='w')
        ttk.Label(outer, textvariable=self.status, wraplength=1250).pack(anchor='w', pady=6)

    def _restore(self):
        path = Path(self.output_root.get())/Path(self.result['source']).stem/'复核记录.json'
        try:
            saved = json.loads(path.read_text(encoding='utf8'))
            if saved['source_sha256'] != self.result['source_sha256'] or Path(saved['source']).resolve()!=Path(self.result['source']).resolve():
                return
            # Only restore names for regions from this extraction; never trust saved geometry.
            overrides = {r['id']: r for r in saved.get('source_regions', [])}
            for region in self.regions:
                old = overrides.get(region['id'], {})
                if (old.get('anchor_ids') == region['anchor_ids'] and old.get('bbox') == region['bbox']
                        and valid_region_name(old.get('name')) and old.get('source_view') in SOURCE_VIEWS):
                    region.update(name=old['name'], source_view=old['source_view'])
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
                    self.patterns[name] = [dict(e, selected_ids=list(e['selected_ids'])) for e in entries]
                    for entry in self.patterns[name]:
                        entry.pop('files', None)
                        self._inherit_region(entry)
                    self.choices[name] = list(ids)
                    self.choice_labels[name] = '、'.join(e.get('region_name', e['source_view']) for e in self.patterns[name])
                    for index, entry in enumerate(self.patterns[name]):
                        self.candidates.insert(0, dict(entry, id=f'saved-{name}-{index}', label='上次已复核成品',
                            category_hints=[name], recommended_categories=[]))
        except (OSError, ValueError, KeyError, TypeError):
            return

    def _region(self, ids):
        return region_for_ids(ids, self.region_assignments, self.regions)

    def _inherit_region(self, entry):
        region = self._region(entry['selected_ids'])
        if region:
            entry.update(source_region=region['id'], region_name=region['name'])
        return entry

    def _split_region_candidates(self):
        if not self.regions:
            return
        candidates = []
        for candidate in self.candidates:
            for index, ids in enumerate(split_by_region(candidate['selected_ids'], self.region_assignments)):
                candidates.append(dict(candidate, id=candidate.get('id', 'region')+f'-region-{index}',
                                       selected_ids=ids))
        self.candidates = candidates

    def _rename_region(self, rid, name, direction):
        if not valid_region_name(name) or direction not in SOURCE_VIEWS:
            raise ValueError('名称限 1–40 字，不能包含路径字符或首尾空格。')
        if any(r['id'] != rid and r['name'] == name for r in self.regions):
            raise ValueError('区域名称不能重复。')
        region = next(r for r in self.regions if r['id'] == rid)
        region.update(name=name, source_view=direction)
        for entries in self.patterns.values():
            for entry in entries:
                if self._region(entry['selected_ids']) is region:
                    entry.update(source_region=rid, region_name=name, source_view=direction)
        for category in self.choices:
            if self.patterns[category]:
                self._sync_patterns(category)
        self._refresh_status()
        self._render_page()
        if self.current_candidate:
            self._preview()

    def _edit_regions(self):
        if self.busy:
            return
        if not self.regions:
            messagebox.showinfo('原稿区域', '本任务暂无 AI 区域。请启用 AI 重新提取；旧任务仍可逐图手动标注来源视图。', parent=self)
            return
        dialog = tk.Toplevel(self)
        dialog.transient(self)
        dialog.grab_set()
        dialog.title('原稿区域 · 修改一次，相关图案统一沿用')
        dialog.geometry('1000x680')
        preview = ImagePreview(dialog)
        preview.pack(fill='both', expand=True)
        row = ttk.Frame(dialog, padding=8); row.pack(fill='x')
        selected = tk.StringVar(value=self.regions[0]['id'])
        chooser = ttk.Combobox(row, textvariable=selected, values=[r['id'] for r in self.regions], state='readonly', width=6)
        chooser.pack(side='left')
        name, direction = tk.StringVar(), tk.StringVar()
        ttk.Entry(row, textvariable=name, width=28).pack(side='left', padx=6)
        ttk.Combobox(row, textvariable=direction, values=SOURCE_VIEWS, state='readonly').pack(side='left')
        ttk.Label(dialog, text='绿色框为原稿区域，红框为当前区域。名称用于文件名和 SVG 图层；方向不确定可保留“待确认视图”。').pack(anchor='w', padx=8)
        def draw(_event=None):
            region = next(r for r in self.regions if r['id'] == selected.get())
            name.set(region['name']); direction.set(region['source_view'])
            from core.fs_scene import source_preview_objects
            try:
                objects = source_preview_objects(self.result)
            except Exception:
                objects = self.result['objects']
            root = ET.fromstring(svg_document(objects, background='#ffffff'))
            ns = '{http://www.w3.org/2000/svg}'
            _, y, width, height = map(float, root.get('viewBox').split())
            for r in self.regions:
                x0, y0, x1, y1 = r['bbox']
                color = '#dc2626' if r['id'] == selected.get() else '#059669'
                top = 2*y+height-y1
                ET.SubElement(root, ns+'rect', {'x': str(x0), 'y': str(top), 'width': str(x1-x0),
                    'height': str(y1-y0), 'fill': 'none', 'stroke': color, 'stroke-width': str(width/600)})
                label = ET.SubElement(root, ns+'text', {'x': str(x0), 'y': str(top+width/65),
                    'font-size': str(width/65), 'fill': color, 'font-family': 'Microsoft YaHei'})
                label.text = r['id']+' · '+r['name']
            preview.set_image(None, VectorImage(ET.tostring(root, encoding='unicode')),
                              vector_label='原稿区域 · 滚轮缩放 / 拖动查看')
        def apply():
            try:
                self._rename_region(selected.get(), name.get(), direction.get())
            except ValueError as exc:
                messagebox.showerror('区域名称无效', str(exc), parent=dialog)
                return
            draw()
        ttk.Button(row, text='应用到本区域全部图案', command=apply).pack(side='left', padx=8)
        chooser.bind('<<ComboboxSelected>>', draw)
        draw()

    def _switch_category(self, name=None):
        self.category = name or self.category
        self.page = 0
        self.current_candidate = None
        self.filtered = []
        seen = set()
        allowed = set(self.by_id)
        compact_graphics = self.category in ('彩条', 'logo', '车漆') and not self.show_all.get()
        for c in self.candidates:
            ids = c.get('selected_ids', [])
            if not ids or not set(ids) <= allowed:
                continue
            if not self.show_all.get() and self.category not in c.get('category_hints', []):
                continue
            # Old manifests also contain broad, geometry-only hints. They
            # must not turn windows, tyres or loose surfaces into results.
            if (compact_graphics and self.category not in c.get('recommended_categories', [])
                    and not c.get('id', '').startswith(('current-', 'manual-', 'saved-', 'legacy-'))):
                continue
            # Preview/export can remove paint or logos from a parent group.
            # Compare the effective selection, not its original ownership.
            effective = self._effective_ids(c)
            key = frozenset(ids if self.show_all.get() else effective)
            if not effective or key in seen:
                continue
            seen.add(key)
            self.filtered.append(c)
        if compact_graphics or (self.category == '门徽' and not self.show_all.get()):
            # Current selections have already been split by source region.
            # Offer the complete classified pattern, not each native subgroup
            # and paint surface contained in that pattern. Keep user choices.
            complete = [set(c['selected_ids']) for c in self.filtered
                        if c.get('id', '').startswith('current-')]
            self.filtered = [c for c in self.filtered
                             if c.get('id', '').startswith(('current-', 'manual-', 'saved-', 'legacy-'))
                             or not any(set(c['selected_ids']) < ids for ids in complete)]
        self.heading.configure(text=LABELS[self.category]+' · 候选图案')
        self._render_page()
        self._refresh_status()

    def _effective_ids(self, candidate):
        """Show the same parent refinement that reviewed exports apply.

        Keep original memberships for undo/reclassification. Confirmed choices
        (including explicit absence) override the model's provisional labels.
        """
        ids = candidate['selected_ids']
        if self.category not in ('车身', '文字'):
            return list(ids)
        categories = self.result.get('component_categories', {})
        from core.paint import category_exclusions
        excluded = category_exclusions(self.category, {
            name: self.choices.get(name, categories.get(name)) for name in LABELS})
        return [oid for oid in ids if oid not in excluded]

    def _selection_label(self, candidate):
        ids = self._effective_ids(candidate)
        removed = len(candidate['selected_ids']) - len(ids)
        removed_label = 'logo' if self.category == '文字' else '车漆等'
        return str(len(ids))+' 条源路径'+(f'（已扣除{removed_label} {removed} 条）' if removed else '')

    def _svg(self, candidate):
        chosen = set(self._effective_ids(candidate))
        return svg_document([o for o in self.result['objects'] if o['id'] in chosen], width=1400)

    def _thumbnail(self, candidate):
        key = tuple(self._effective_ids(candidate))
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
            region = self._region(c['selected_ids'])
            region_label = ' · '+region['name'] if region else ''
            self.tree.insert('', 'end', iid=str(index), image=photo,
                             text=' '+prefix+c.get('label',c['id'])+region_label+'\n '+self._selection_label(c))
        pages = max(1,(len(self.filtered)+self.PAGE_SIZE-1)//self.PAGE_SIZE)
        self.page_label.configure(text=f'{self.page+1}/{pages} 页 · {len(self.filtered)} 套')
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0]); self._preview()
        else:
            self.current_candidate = None
            self.preview.set_image(None)
            self._request_location()
            self.status.set('该类暂无分组候选，可直接点击“从零自由选取新图案”，在整张原稿上点选或框选。')

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
        direction = (saved or self.current_candidate).get('source_view', SOURCE_VIEWS[0])
        region = self._region(self.current_candidate['selected_ids'])
        if region and not saved:
            direction = region['source_view']
        if direction == SOURCE_VIEWS[0] and not region:
            direction = infer_source_view(self.current_candidate['selected_ids'], self.result['objects'],
                                          self.candidates + [e for entries in self.patterns.values() for e in entries])
        self.source_view.set(direction)
        vector = VectorImage(self._svg(self.current_candidate))
        self.preview.set_image(None, vector, vector_label=('来源区域：'+region['name']+' · 自动继承名称' if region else '候选 SVG · 待确认区域，可手动标注来源视图'))
        self._request_location()

    def _request_location(self):
        self._locator_generation += 1
        selected = [self.by_id[i] for i in self._effective_ids(self.current_candidate or {'selected_ids': []})
                    if i in self.by_id and self.by_id[i].get('bbox')]
        # Clear the previous candidate's red box while the new one is loading.
        self.source_locator.show_prepared(None, None, None)
        self.source_locator.heading.configure(text='原稿位置 · 正在加载…' if selected else '原稿位置 · 无候选图案')
        try:
            self._locator_requests.get_nowait()
        except queue.Empty:
            pass
        self._locator_requests.put_nowait((self._locator_generation,
                                          union_bounds(selected) if selected else None))

    @staticmethod
    def _locator_worker(result, requests, events, stop):
        # Only plain data and PIL images cross this boundary; Tk stays on the UI thread.
        renderer, page, title = SourceLocatorRenderer(), None, ''
        while not stop.is_set():
            try:
                generation, bounds = requests.get(timeout=.2)
            except queue.Empty:
                continue
            if bounds is None:
                events.put(('locator', (generation, None, None, None, '原稿位置 · 无候选图案')))
                continue
            try:
                if page is None:
                    try:
                        from core.fs_scene import source_preview_objects
                        objects = source_preview_objects(result)
                        title = '原稿位置 · 第 1 页 · 红框为当前图案'
                    except Exception:
                        objects = result['objects']
                        title = '原稿位置 · 提取对象预览（原稿读取失败）'
                    page = {'objects': objects, 'image': VectorImage(
                        svg_document(objects, background='#ffffff'))}
                image = renderer.prepare(page, bounds)
                payload = (generation, image, renderer._viewbox, renderer._viewport, title)
            except Exception:
                payload = (generation, None, None, None, '原稿位置 · 预览不可用')
            if not stop.is_set():
                events.put(('locator', payload))

    def _confirm(self):
        if self.busy or not self.current_candidate:
            return
        self._add_pattern(self.category, self.current_candidate['selected_ids'])
        self._refresh_status()

    def _add_pattern(self, category, ids):
        if category == '彩条':
            self.region_assignments.update(object_regions(
                [self.by_id[i] for i in ids], self.regions, partial_ids=ids))
        groups = split_by_region(ids, self.region_assignments)
        if len(groups) > 1:
            direction = self.source_view.get()
            for group in groups:
                region = self._region(group)
                self.source_view.set(region['source_view'] if region else SOURCE_VIEWS[0])
                self._add_pattern(category, group)
            self.source_view.set(direction)
            return
        entry = {'selected_ids': list(ids), 'source_view': self.source_view.get()}
        self._inherit_region(entry)
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
        self.choice_labels[category] = '、'.join(e.get('region_name', e['source_view']) for e in entries) if entries else '无图案'

    def _preview_pattern(self, _event=None):
        rows = self.pattern_list.curselection()
        if not rows or self.busy:
            return
        self.current_candidate = self.patterns[self.category][rows[0]]
        self.source_view.set(self.current_candidate['source_view'])
        self.preview.set_image(None, VectorImage(self._svg(self.current_candidate)),
                               vector_label='已添加图案 · 修改来源视图后点击更新')
        self._request_location()

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

    def _edit_objects(self, *, from_scratch=False):
        if self.busy:
            return
        category = self.category
        initial = [] if from_scratch else self._effective_ids(self.current_candidate or {'selected_ids': []})
        ObjectSelectionEditor(self, self.result['objects'], initial or [],
                              '' if from_scratch else LABELS[category],
                              lambda ids: self._accept_manual(category, ids),
                              on_category_confirm=self._accept_manual)

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
            self.pattern_list.insert('end', f"{index:02d} · {entry.get('region_name', entry['source_view'])} · {self._selection_label(entry)}")
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
        for widget in (self.export_button,self.confirm_button,self.absent_button,self.edit_button,self.new_pattern_button,self.browse_button,self.output_entry):
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
            if kind == 'locator':
                generation, image, viewbox, viewport, title = value
                if generation == self._locator_generation:
                    self.source_locator.heading.configure(text=title)
                    self.source_locator.show_prepared(image, viewbox, viewport)
                self._poll_id = self.after(100, self._drain)
                return
            if kind in ('candidates','candidate_error'):
                if kind=='candidates':
                    self.candidates.extend(value)
                    self._switch_category(self.category)
                else:
                    self.status.set('旧任务候选补充失败：'+value+'；仍可复核已保存结果。')
                self._poll_id=self.after(100,self._drain)
                return
            self.busy = False
            for widget in (self.export_button,self.confirm_button,self.absent_button,self.edit_button,self.new_pattern_button,self.browse_button,self.output_entry):
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
        self._locator_stop.set()
        poll_id=getattr(self,'_poll_id',None)
        if poll_id is not None:
            try: self.after_cancel(poll_id)
            except tk.TclError: pass
            self._poll_id=None
        super().destroy()
