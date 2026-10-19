"""Chinese Tk desktop front end for the independent livery extraction engine."""
from __future__ import annotations

import os
import subprocess
import json
import hashlib
from pathlib import Path
import queue
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image
from image_preview import ImagePreview
from hover_choice import HoverChoice
from source_locator import SourceLocator
from vector_preview import VectorDocument, VectorImage
from runtime import APP_DIR


class LiveryApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("全车矢量提取工作台")
        root.geometry("1280x880")
        root.minsize(960, 680)
        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.busy = False
        self.sources: dict[str, Path] = {}
        self.results: dict[str, dict] = {}
        self.selections: dict[str, set[str]] = {}
        self.current: str | None = None
        self.preview_images = {}
        self._source_preview_key = None
        self._source_vector = None
        self._active_group = None
        self.output = tk.StringVar(value=str(APP_DIR / "output"))
        self.five_components = tk.BooleanVar(value=True)
        self.concurrency = tk.IntVar(value=min(os.cpu_count() or 4, 32))
        self.use_ai = tk.BooleanVar(value=False)
        self.ai_status = tk.StringVar(value="AI 未配置")
        self.reference_directory = tk.StringVar()
        self.reference_status = tk.StringVar(value="独立识别：直接分析源曲线，不使用参考库")
        self.show_all = tk.BooleanVar(value=False)
        self.object_filter = tk.StringVar()
        self.locate_id = tk.StringVar()
        self.object_page = 0
        self.object_page_size = 300
        self._filter_after = None
        self._object_cache_key = None
        self._object_rows = []
        self._object_index = {}
        self._matching_rows = []
        self._matching_key = None
        self.method_status = tk.StringVar(value="识别结果均需人工复核")
        self.object_warning = tk.StringVar(value="选中列表行可查看对象警告。")
        self.status = tk.StringVar(value="添加 FS 文件后开始。识别结果可在右侧补选、排除。")
        self._build()
        self._refresh_ai()
        self.root.after(100, self._drain)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self):
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill="x")
        self.add_file_button = ttk.Button(toolbar, text="添加 FS 文件", command=self._add_files)
        self.add_file_button.pack(side="left")
        self.add_folder_button = ttk.Button(toolbar, text="添加文件夹", command=self._add_folder)
        self.add_folder_button.pack(side="left", padx=6)
        ttk.Button(toolbar, text='载入已提取任务', command=self._load_extracted_task).pack(side='left', padx=(0,6))
        self.remove_button = ttk.Button(toolbar, text="移除所选", command=self._remove)
        self.remove_button.pack(side="left")
        self.start_button = ttk.Button(toolbar, text="开始批量提取", command=self._start)
        self.start_button.pack(side="right")
        self.stop_button = ttk.Button(toolbar, text="停止后续任务", command=self._stop, state="disabled")
        self.stop_button.pack(side="right", padx=6)
        self.concurrency_spin = ttk.Spinbox(toolbar, from_=1, to=max(64, (os.cpu_count() or 4) * 2), textvariable=self.concurrency, width=4)
        self.concurrency_spin.pack(side="right", padx=(2, 6))
        self.concurrency_label = ttk.Label(toolbar, text="并发任务数:")
        self.concurrency_label.pack(side="right")

        output_row = ttk.Frame(outer)
        output_row.pack(fill="x", pady=(10, 6))
        ttk.Label(output_row, text="输出目录").pack(side="left")
        ttk.Entry(output_row, textvariable=self.output).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Button(output_row, text="浏览…", command=self._browse_output).pack(side="left")
        ttk.Button(output_row, text="打开输出目录", command=self._open_output).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(outer, text="六类提取：车身 / 车漆 / 彩条 / 文字 / logo / 门徽（候选需逐类复核；取消则仅提取彩条）",
                        variable=self.five_components).pack(anchor="w")
        ai_row = ttk.Frame(outer)
        ai_row.pack(fill="x", pady=(4, 0))
        self.ai_config_button = ttk.Button(ai_row, text="AI 接口配置…", command=self._configure_ai)
        self.ai_config_button.pack(side="left")
        ttk.Label(ai_row, textvariable=self.ai_status).pack(side="left", padx=8)
        reference_row = ttk.Frame(outer)
        reference_row.pack(fill="x", pady=(4, 0))
        self.reference_button = ttk.Button(reference_row, text="参考库文件夹…", command=self._configure_reference)
        self.reference_reset_button = ttk.Button(reference_row, text="恢复自动查找", command=lambda: self._save_reference_directory(""))
        ttk.Label(reference_row, textvariable=self.reference_status).pack(side="left", padx=2)

        split = ttk.Panedwindow(outer, orient="horizontal")
        split.pack(fill="both", expand=True, pady=8)
        left = ttk.Frame(split)
        right = ttk.Frame(split)
        split.add(left, weight=1)
        split.add(right, weight=3)
        ttk.Label(left, text="任务列表").pack(anchor="w", pady=(0, 4))
        self.tasks = ttk.Treeview(left, columns=("name", "status"), show="headings", selectmode="extended", height=10)
        self.tasks.heading("name", text="文件")
        self.tasks.heading("status", text="状态")
        self.tasks.column("name", width=235)
        self.tasks.column("status", width=145, stretch=False)
        self.tasks.pack(fill="both", expand=True)
        self.tasks.bind("<<TreeviewSelect>>", self._task_selected)

        ttk.Label(left, text="运行日志").pack(anchor="w", pady=(10, 4))
        log_frame = ttk.Frame(left)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, width=40, height=12, wrap="word", state="disabled", font=("Microsoft YaHei UI", 9))
        log_scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)

        # Only the preview and object list absorb height changes. Fixed controls
        # must keep their requested height, including on high-DPI displays.
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=3)
        right.rowconfigure(4, weight=1)
        self.previews = ttk.Notebook(right)
        self.previews.grid(row=0, column=0, sticky="nsew")
        self.canvases = {}
        self.preview_viewers = {}
        for key, label in (("preview_png", "整车原图"), ("candidate_png", "候选对象"), ("result_png", "提取结果")):
            frame = ImagePreview(self.previews)
            self.preview_viewers[key] = frame
            self.canvases[key] = frame.canvas
            self.previews.add(frame, text=label)

        self.source_locator = SourceLocator(self.preview_viewers['result_png'].canvas)
        self.source_locator.place(x=8, y=8)

        group_bar=ttk.Frame(right)
        group_bar.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(group_bar,text='切换候选组合').pack(side='left')
        self.group_choice=HoverChoice(group_bar,state='readonly',width=36,
                                      on_hover=self._choose_group)
        self.group_choice.pack(side='left',padx=8)
        self.group_choice.bind('<<ComboboxSelected>>',self._choose_group)
        ttk.Label(group_bar, text='移入展开 · 悬浮切换预览', foreground='#666666').pack(side='left')

        ttk.Label(right, textvariable=self.method_status, foreground="#7a4d00", wraplength=850).grid(row=2, column=0, sticky="w", pady=(4, 0))
        object_bar = ttk.Frame(right)
        object_bar.grid(row=3, column=0, sticky="ew", pady=(8, 4))
        ttk.Label(object_bar, text="矢量对象").pack(side="left")
        ttk.Checkbutton(object_bar, text="显示全部", variable=self.show_all, command=self._populate_objects).pack(side="left", padx=8)
        ttk.Label(object_bar, text="筛选 ID / 颜色").pack(side="left")
        ttk.Entry(object_bar, textvariable=self.object_filter, width=20).pack(side="left", padx=6)
        self.object_filter.trace_add("write", self._schedule_filter)
        self.selection_label = ttk.Label(object_bar, text="已选 0")
        self.selection_label.pack(side="right")
        object_frame = ttk.Frame(right)
        object_frame.grid(row=4, column=0, sticky="nsew")
        self.objects = ttk.Treeview(object_frame, columns=("chosen", "id", "fill", "score", "bbox"), show="headings", selectmode="extended", height=7)
        for key, label, width in (("chosen", "保留", 45), ("id", "对象 ID", 130), ("fill", "填充", 85), ("score", "评分", 65), ("bbox", "边界", 230)):
            self.objects.heading(key, text=label)
            self.objects.column(key, width=width, stretch=key in ("id", "bbox"))
        object_scroll = ttk.Scrollbar(object_frame, command=self.objects.yview)
        self.objects.configure(yscrollcommand=object_scroll.set)
        object_scroll.pack(side="right", fill="y")
        self.objects.pack(fill="both", expand=True)
        self.objects.bind("<Double-1>", self._toggle_object)
        self.objects.bind("<<TreeviewSelect>>", self._show_object_warnings)
        page_bar = ttk.Frame(right)
        page_bar.grid(row=5, column=0, sticky="ew", pady=(4, 0))
        self.prev_page_button = ttk.Button(page_bar, text="上一页", command=lambda: self._change_page(-1))
        self.prev_page_button.pack(side="left")
        self.next_page_button = ttk.Button(page_bar, text="下一页", command=lambda: self._change_page(1))
        self.next_page_button.pack(side="left", padx=5)
        self.page_label = ttk.Label(page_bar, text="匹配 0 个 · 第 0 / 0 页")
        self.page_label.pack(side="left", padx=6)
        locate_bar = ttk.Frame(right)
        locate_bar.grid(row=6, column=0, sticky="ew", pady=(4, 0))
        ttk.Label(locate_bar, text="对象 ID").pack(side="left", padx=(0, 3))
        locate_entry = ttk.Entry(locate_bar, textvariable=self.locate_id, width=16)
        locate_entry.pack(side="left")
        locate_entry.bind("<Return>", lambda _: self._locate_object())
        ttk.Button(locate_bar, text="定位", command=self._locate_object).pack(side="left", padx=4)
        ttk.Button(locate_bar, text="按 ID 补选", command=lambda: self._locate_object(include=True)).pack(side="left")
        ttk.Label(right, textvariable=self.object_warning, foreground="#8a4b00", wraplength=850).grid(row=7, column=0, sticky="w", pady=(3, 0))
        actions = ttk.Frame(right)
        actions.grid(row=8, column=0, sticky="ew", pady=(6, 0))
        actions.columnconfigure(0, weight=1)
        selection_actions = ttk.Frame(actions)
        selection_actions.grid(row=0, column=0, sticky="w")
        ttk.Button(selection_actions, text="补选对象", command=lambda: self._change_selection(True)).pack(side="left")
        ttk.Button(selection_actions, text="排除对象", command=lambda: self._change_selection(False)).pack(side="left", padx=(6, 0))
        export_actions = ttk.Frame(actions)
        export_actions.grid(row=0, column=1, sticky="e")
        self.save_button = ttk.Button(export_actions, text="保存修正 SVG / PNG", command=self._save)
        self.save_button.pack(side="right")
        self.review_button = ttk.Button(export_actions, text='六类图案复核 / 导出成品', command=self._open_review_gallery)
        self.review_button.pack(side='right', padx=(0, 6))

        def arrange_actions(event):
            stacked = event.width < selection_actions.winfo_reqwidth() + export_actions.winfo_reqwidth() + 12
            export_actions.grid_configure(row=1 if stacked else 0,
                                          column=0 if stacked else 1,
                                          pady=(6, 0) if stacked else 0)

        actions.bind("<Configure>", arrange_actions)
        ttk.Label(right, text="双击对象切换保留状态；预览在保存修正后更新。", foreground="#666666").grid(row=9, column=0, sticky="w", pady=(5, 0))
        self.progress = ttk.Progressbar(outer, mode="determinate")
        self.progress.pack(side="bottom", fill="x", before=split)
        ttk.Label(outer, textvariable=self.status, wraplength=1100).pack(side="bottom", anchor="w", pady=(5, 0), before=split)

    def _load_extracted_task(self):
        if self.busy:
            return
        filename=filedialog.askopenfilename(title='选择提取任务的 manifest.json', filetypes=[('提取任务 manifest','*.json')])
        if not filename:
            return
        try:
            from engine import _load_version, _file_sha
            path=Path(filename).resolve()
            data=json.loads(path.read_text(encoding='utf8'))
            pointer=data if 'manifest_sha256' in data else {'version_manifest':str(path),'manifest_sha256':_file_sha(path)}
            result=_load_version(pointer)
            source=Path(result['source'])
            if hashlib.sha256(source.read_bytes()).hexdigest()!=result['source_sha256']:
                raise ValueError('源 FS 已变化，请重新提取。')
            item=self.tasks.insert('', 'end',values=(source.name,'已载入·待复核'))
            self.sources[item]=source
            self.results[item]=result
            self.selections[item]=set(result['selected_ids'])
            self.tasks.selection_set(item)
            self.current=item
            self._populate_objects(); self._load_previews(result); self._update_groups()
            self._open_review_gallery()
        except Exception as exc:
            messagebox.showerror('无法载入任务',str(exc))

    def _open_review_gallery(self):
        if self.busy or self.current not in self.results:
            messagebox.showinfo('选择提取任务', '请先选择一份已完成提取的 FS 任务。')
            return
        from review_gallery import ReviewGallery
        result=self.results[self.current]
        manual=None
        if self._active_group and self._active_group[0]==self.current:
            view=result['views'][self._active_group[1]]
            category=view.get('category','彩条')
            ids=sorted(self.selections.get(self.current,set()))
            if ids:
                manual={'id':'manual-current','label':'当前人工补选结果','selected_ids':ids,
                        'category_hints':[category],'recommended_categories':[]}
        ReviewGallery(self.root, result, Path(self.output.get())/'已复核成品', manual)

    def _add_files(self):
        paths = filedialog.askopenfilenames(title="选择 FS 文件", filetypes=[("FlexiSIGN 文件", "*.fs *.FS"), ("所有文件", "*.*")])
        self._add_paths(Path(p) for p in paths)

    def _add_folder(self):
        folder = filedialog.askdirectory(title="选择素材文件夹（含子目录）")
        if folder:
            try:
                self._add_paths(p for p in Path(folder).rglob("*") if p.is_file() and p.suffix.lower() == ".fs")
            except OSError as exc:
                messagebox.showerror("读取文件夹失败", str(exc))

    def _add_paths(self, paths):
        count = 0
        for p in paths:
            p = p.resolve()
            if p.suffix.lower() != ".fs" or p in self.sources.values():
                continue
            item = self.tasks.insert("", "end", values=(p.name, "待处理"))
            self.sources[item] = p
            count += 1
        self.status.set(f"已添加 {count} 份；共 {len(self.sources)} 份 FS 文件。")

    def _remove(self):
        if self.busy:
            return
        for item in self.tasks.selection():
            self.tasks.delete(item)
            self.sources.pop(item, None)
            self.results.pop(item, None)
            self.selections.pop(item, None)
        self.current = None
        self._populate_objects()
        self._load_previews({})

    def _browse_output(self):
        folder = filedialog.askdirectory(title="选择输出目录")
        if folder:
            self.output.set(folder)

    def _refresh_ai(self):
        try:
            from vision import read_config, configuration_status
            config = read_config()
            self.use_ai.set(config.get("enabled") is True)
            self.ai_status.set("AI：" + configuration_status(config))
            self.reference_directory.set("")
            self.reference_status.set("独立识别：直接分析源曲线，不使用参考库")
        except Exception as exc:
            self.use_ai.set(False)
            self.ai_status.set("AI 配置无效：" + str(exc))

    def _configure_reference(self):
        if self.busy:
            return
        folder = filedialog.askdirectory(title="选择导出彩条参考库文件夹", initialdir=self.reference_directory.get() or str(APP_DIR))
        if folder:
            self._save_reference_directory(str(Path(folder).resolve()))

    def _save_reference_directory(self, directory):
        if self.busy:
            return
        try:
            from vision import CONFIG_PATH
            existing = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig")) if CONFIG_PATH.exists() else {}
            if not isinstance(existing, dict):
                raise ValueError("settings.json 必须是 JSON 对象。")
            if existing.get("api_key"):
                raise ValueError("配置文件含明文 api_key，请先迁移至环境变量并移除 api_key；界面不会重写密钥。")
            existing["reference_directory"] = directory
            temporary = CONFIG_PATH.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(CONFIG_PATH)
        except (OSError, ValueError) as exc:
            messagebox.showerror("参考目录未保存", str(exc))
            return
        self.reference_directory.set(directory)
        self.reference_status.set(directory or "自动查找：素材同目录 / 导出彩条参考")
        self.status.set("参考库目录已保存；后续提取将使用新参考。")

    def _configure_ai(self):
        if self.busy:
            return
        try:
            from vision import CONFIG_PATH, DEFAULTS, read_config, configuration_status, test_connection
            config = read_config()
        except Exception as exc:
            messagebox.showerror("AI 配置错误", str(exc))
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("AI 视觉接口配置")
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        fields = {}
        for index, (name, label) in enumerate((("base_url", "API 根地址（含 /v1）"), ("model", "视觉模型名称"), ("api_key_env", "密钥环境变量名称"))):
            ttk.Label(frame, text=label).grid(row=index, column=0, sticky="w", pady=6)
            value = tk.StringVar(value=config.get(name, ""))
            ttk.Entry(frame, textvariable=value, width=54).grid(row=index, column=1, sticky="ew", padx=(12, 0))
            fields[name] = value
        enabled = tk.BooleanVar(value=config.get("enabled", False))
        use_system_proxy = tk.BooleanVar(value=config.get("use_system_proxy", True))
        secret_value=tk.StringVar()
        ttk.Label(frame,text='更新本机密钥（留空保留）').grid(row=3,column=0,sticky='w',pady=6)
        ttk.Entry(frame,textvariable=secret_value,show='•',width=54).grid(row=3,column=1,sticky='ew',padx=(12,0))
        ttk.Checkbutton(frame, text="启用 AI 视觉识别", variable=enabled).grid(row=4, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Checkbutton(frame, text="使用系统代理（关闭后仅本工具直连接口）", variable=use_system_proxy).grid(row=5, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Label(frame, text="启用后，任务会把整车图、候选图和源对象信息发往上方接口，独立识别，不使用参考库。\n本机密钥使用 Windows 当前用户加密保存；环境变量优先。\n保存配置不会测试连接或发送图片。", wraplength=540, foreground="#666666").grid(row=6, column=0, columnspan=2, sticky="w", pady=8)

        def save():
            updated = {name: config.get(name, default) for name, default in DEFAULTS.items()}
            updated.update({name: value.get().strip() for name, value in fields.items()})
            updated["enabled"] = enabled.get()
            updated["use_system_proxy"] = use_system_proxy.get()
            try:
                # Validate locally before writing; no credential or endpoint requests.
                if updated["enabled"]:
                    configuration_status(updated)
                if not updated["api_key_env"] or not updated["api_key_env"].replace("_", "a").isalnum() or updated["api_key_env"][0].isdigit():
                    raise ValueError("密钥环境变量名称无效。")
                # Never rewrite a local inline credential through the UI.
                existing = {}
                if CONFIG_PATH.exists():
                    existing = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
                if existing.get("api_key"):
                    raise ValueError("配置文件含本地明文 api_key。请先将密钥迁移到环境变量并移除 api_key，再通过界面保存；现有配置没有修改。")
                existing.update(updated)
                if secret_value.get().strip():
                    from credentials import save_api_key
                    save_api_key(secret_value.get())
                    secret_value.set('')
                temporary = CONFIG_PATH.with_suffix(".json.tmp")
                temporary.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                temporary.replace(CONFIG_PATH)
            except (OSError, ValueError, RuntimeError) as exc:
                messagebox.showerror("无法保存配置", str(exc), parent=dialog)
                return
            self._refresh_ai()
            dialog.destroy()

        test_status = tk.StringVar(value="")
        ttk.Label(frame, textvariable=test_status, foreground="#0066cc", wraplength=540).grid(row=7, column=0, columnspan=2, sticky="w", pady=(0, 6))

        def test_conn():
            test_button.configure(state="disabled", text="正在测试连接…")
            test_status.set("正在连接并验证接口地址、模型与密钥…")
            test_config = {name: config.get(name, default) for name, default in DEFAULTS.items()}
            test_config.update({name: value.get().strip() for name, value in fields.items()})
            test_config["enabled"] = True
            test_config["use_system_proxy"] = use_system_proxy.get()
            if secret_value.get().strip():
                test_config["api_key"] = secret_value.get().strip()

            def worker():
                try:
                    res = test_connection(test_config)
                    dialog.after(0, lambda: on_finish(True, res.get("message", "连接成功！")))
                except Exception as err:
                    dialog.after(0, lambda msg=str(err): on_finish(False, msg))

            def on_finish(ok, msg):
                test_button.configure(state="normal", text="测试连接")
                if ok:
                    test_status.set("✓ 连接成功！接口响应正常。")
                    messagebox.showinfo("测试连接成功", msg, parent=dialog)
                else:
                    test_status.set("✗ 连接失败：" + msg.splitlines()[0])
                    messagebox.showerror("测试连接失败", msg, parent=dialog)

            threading.Thread(target=worker, daemon=True).start()

        actions = ttk.Frame(frame)
        actions.grid(row=8, column=0, columnspan=2, sticky="e")
        test_button = ttk.Button(actions, text="测试连接", command=test_conn)
        test_button.pack(side="left", padx=(0, 16))
        ttk.Button(actions, text="取消", command=dialog.destroy).pack(side="left", padx=6)
        ttk.Button(actions, text="保存配置", command=save).pack(side="left")

    def _open_file_location(self, target_path=None):
        if target_path is None:
            if self.current in self.results:
                res = self.results[self.current]
                svg = res.get("result_svg")
                if svg and Path(svg).is_file():
                    target_path = Path(svg)
                else:
                    target_path = Path(res.get("output_dir") or self.output.get())
            else:
                target_path = Path(self.output.get())

        target = Path(target_path).expanduser().resolve()
        if not target.exists():
            messagebox.showinfo("打开文件位置", "文件或目录尚未生成，请先完成一次提取或保存。")
            return
        try:
            if target.is_file():
                subprocess.Popen(f"explorer.exe /select,\"{target}\"", shell=True)
            else:
                subprocess.Popen(f"explorer.exe \"{target}\"", shell=True)
        except Exception as exc:
            messagebox.showerror("无法打开文件位置", str(exc))

    def _open_output(self):
        self._open_file_location()

    def _set_busy(self, busy):
        self.busy = busy
        for button in (self.start_button, self.add_file_button, self.add_folder_button, self.remove_button, self.save_button, self.ai_config_button, self.reference_button, self.reference_reset_button):
            button.configure(state="disabled" if busy else "normal")
        if hasattr(self, "concurrency_spin"):
            self.concurrency_spin.configure(state="disabled" if busy else "normal")
        self.group_choice.configure(state="disabled" if busy else "readonly")
        self.stop_button.configure(state="normal" if busy else "disabled")

    def _start(self):
        if self.busy:
            return
        if not self.sources:
            messagebox.showinfo("添加素材", "请先添加 FS 文件或文件夹。")
            return
        if not self.output.get().strip():
            messagebox.showinfo("输出目录", "请选择输出目录。")
            return
        try:
            concurrency = max(1, int(self.concurrency.get()))
        except (ValueError, tk.TclError):
            concurrency = os.cpu_count() or 4
            self.concurrency.set(concurrency)
        output = Path(self.output.get()).expanduser().resolve()
        jobs = list(self.sources.items())
        self._refresh_ai()
        use_ai = self.use_ai.get()
        reference_directory = self.reference_directory.get()
        self.stop_event.clear()
        self.progress.configure(value=0, maximum=len(jobs))
        self._set_busy(True)
        self.status.set(f"正在启动 {min(concurrency, len(jobs))} 个并发任务…")
        mode = 'components' if self.five_components.get() else 'stripes'
        threading.Thread(target=self._batch_worker, args=(jobs, output, True, use_ai, reference_directory, concurrency, mode), daemon=True).start()

    def _batch_worker(self, jobs, output, force=False, use_ai=False, reference_directory="", concurrency=1, extraction_mode='stripes'):
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from engine import process_file
            output.mkdir(parents=True, exist_ok=True)
            worker_count = max(1, min(int(concurrency), len(jobs)))
            self.events.put(("log", f"本批次同时处理 {worker_count} 个文件，共 {len(jobs)} 个文件。"))
            def _worker_task(item, source):
                if self.stop_event.is_set():
                    return
                self.events.put(("running", item, source.name))
                try:
                    result = process_file(
                        source,
                        output,
                        options={"force": force, "use_ai": use_ai, "reference_directory": reference_directory,
                                 "extraction_mode": extraction_mode},
                        progress=lambda text: self.events.put(("log", f"[{source.name}] {text}"))
                    )
                    self.events.put(("result", item, result))
                except Exception as exc:
                    self.events.put(("failed", item, str(exc)))
                    self.events.put(("log", f"[{source.name}] 失败：{exc}\n{traceback.format_exc()}"))
                finally:
                    self.events.put(("step",))

            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {}
                for item, source in jobs:
                    if self.stop_event.is_set():
                        break
                    futures[executor.submit(_worker_task, item, source)] = (item, source)
                for fut in as_completed(futures):
                    if self.stop_event.is_set():
                        executor.shutdown(wait=False, cancel_futures=True)
                        break
        except Exception as exc:
            self.events.put(("error", f"提取引擎无法启动：{exc}\n请确认 engine.py 及其依赖已安装。"))
        finally:
            self.events.put(("done", "已停止后续任务。" if self.stop_event.is_set() else "批量处理结束。可逐项查看结果并修正。"))

    def _stop(self):
        self.stop_event.set()
        self.status.set("已请求停止，正在等待当前运行中的任务结束…")
        self.stop_button.configure(state="disabled")

    def _task_selected(self, _event=None):
        items = self.tasks.selection()
        self.current = items[0] if items else None
        self._populate_objects()
        self._load_previews(self.results.get(self.current, {}))
        self._update_groups()

    def _update_groups(self):
        self._active_group = None
        groups=self.results.get(self.current,{}).get('views',[])
        self.group_choice.configure(values=[f'{g["label"]} · {len(g["selected_ids"])} 条曲线' for g in groups])
        is_components = self.results.get(self.current,{}).get('extraction_mode') == 'components'
        self.group_choice.set(('切换车身 / 车漆 / 彩条 / 文字 / logo / 门徽候选' if is_components else '可切换左右侧及前后视图候选') if groups else '暂无候选组合')

    def _choose_group(self,_event=None):
        if self.busy or self.current not in self.results:
            return
        index=self.group_choice.current()
        groups=self.results[self.current].get('views',[])
        if index<0 or index>=len(groups):return
        if self._active_group == (self.current, index):
            return
        self._active_group = (self.current, index)
        group=groups[index]
        self.selections[self.current]=set(group['selected_ids'])
        self._populate_objects()
        preview=dict(self.results[self.current],result_png=group['png'],result_svg=group['svg'],
                     selected_ids=group['selected_ids'])
        self._load_previews(preview, result_only=True)
        self.previews.select(self.preview_viewers['result_png'])
        self.status.set('已切换候选组合，尚未保存。请核对后保存修正。')

    def _schedule_filter(self, *_):
        if self._filter_after is not None:
            self.root.after_cancel(self._filter_after)
        self._filter_after = self.root.after(200, self._apply_filter)

    def _apply_filter(self):
        self._filter_after = None
        self._populate_objects()

    def _prepare_object_rows(self):
        result = self.results.get(self.current, {})
        source = result.get("objects", [])
        cache_key = (self.current, id(source), len(source))
        if cache_key == self._object_cache_key:
            return
        self._object_cache_key = cache_key
        self._matching_key = None
        self._object_rows = []
        self._object_index = {}
        for obj in source:
            oid = str(obj["id"])
            try:
                score = float(obj.get("score", 0) or 0)
            except (TypeError, ValueError):
                score = 0
            bounds = ", ".join(f"{v:.1f}" if isinstance(v, (float, int)) else str(v) for v in obj.get("bbox", []))
            row = (oid, score, f"{oid} {obj.get('fill', '')}".lower(), bounds, obj)
            self._object_rows.append(row)
            self._object_index[oid] = row
            # Source IDs are aliases only when unambiguous in this document.
            for key in ("object_id", "source_object_id"):
                if obj.get(key) is not None:
                    alias = str(obj[key])
                    if alias != oid:
                        self._object_index[alias] = row if alias not in self._object_index else None

    def _populate_objects(self, reset_page=True):
        if self._filter_after is not None:
            self.root.after_cancel(self._filter_after)
            self._filter_after = None
        self._prepare_object_rows()
        chosen = self.selections.get(self.current, set())
        needle = self.object_filter.get().strip().lower()
        matching_key = (self._object_cache_key, needle, self.show_all.get(), frozenset(chosen))
        if matching_key != self._matching_key:
            self._matching_rows = [row for row in self._object_rows
                if (self.show_all.get() or row[1] > 0 or row[0] in chosen)
                and (not needle or needle in row[2])]
            self._matching_key = matching_key
        if reset_page:
            self.object_page = 0
        self._render_object_page()
        self._update_method_status()

    def _render_object_page(self):
        old_focus = self.objects.focus()
        self.objects.delete(*self.objects.get_children())
        chosen = self.selections.get(self.current, set())
        total = len(self._matching_rows)
        pages = (total + self.object_page_size - 1) // self.object_page_size
        self.object_page = max(0, min(self.object_page, max(0, pages - 1)))
        start = self.object_page * self.object_page_size
        for oid, score, _, bounds, obj in self._matching_rows[start:start + self.object_page_size]:
            self.objects.insert("", "end", iid=oid, values=("✓" if oid in chosen else "", oid,
                obj.get("fill") or "未知", f"{score:.3f}", bounds))
        if old_focus and self.objects.exists(old_focus):
            self.objects.focus(old_focus)
            self.objects.selection_set(old_focus)
        self.selection_label.configure(text=f"已选 {len(chosen)} / 共 {len(self._object_rows)}")
        self.page_label.configure(text=f"匹配 {total:,} 个 · 第 {self.object_page + 1 if pages else 0} / {pages} 页 · 每页 {self.object_page_size}")
        self.prev_page_button.configure(state="normal" if self.object_page > 0 else "disabled")
        self.next_page_button.configure(state="normal" if self.object_page + 1 < pages else "disabled")
        self._show_object_warnings()

    def _change_page(self, delta):
        self.object_page += delta
        self._render_object_page()

    def _locate_object(self, include=False):
        if self.current not in self.results or (include and self.busy):
            return
        self._prepare_object_rows()
        requested = self.locate_id.get().strip()
        row = self._object_index.get(requested)
        if row is None:
            self.object_warning.set(f"未找到唯一对象 ID：{requested}。请输入列表中的完整 ID。")
            return
        oid = row[0]
        if include:
            self.selections.setdefault(self.current, set()).add(oid)
        # Exact location deliberately clears text filtering and exposes all rows.
        self.show_all.set(True)
        self.object_filter.set("")
        self._populate_objects()
        self.object_page = next(i for i, candidate in enumerate(self._matching_rows) if candidate[0] == oid) // self.object_page_size
        self._render_object_page()
        self.objects.selection_set(oid)
        self.objects.focus(oid)
        self.objects.see(oid)
        self._show_object_warnings()
        self.status.set(f"已{'补选并' if include else ''}定位对象 {oid}。" + ("尚未保存修正。" if include else ""))

    def _show_object_warnings(self, _event=None):
        messages = []
        for oid in self.objects.selection():
            row = self._object_index.get(oid)
            if not row:
                continue
            obj = row[4]
            warnings = obj.get("warnings") or []
            if isinstance(warnings, str):
                warnings = [warnings]
            details = list(map(str, warnings))
            fill_status = obj.get("fill_status")
            if fill_status and fill_status != "stored_display_rgb":
                details.append(f"填充状态：{fill_status}")
            if not obj.get("fill"):
                details.append("填充颜色未知，需核对导出结果")
            if obj.get("visibility") == "unknown":
                details.append("原始隐藏状态未确立")
            if obj.get("requires_boolean_semantics"):
                details.append("涉及组合/裁剪语义，需人工复核")
            messages.append(f"{oid}：" + ("；".join(dict.fromkeys(details)) or "无对象级警告；仍需与整车原图核对。"))
            if len(messages) >= 3:
                break
        self.object_warning.set(" | ".join(messages) if messages else "选中列表行可查看对象警告；翻页不会丢失已保留对象。")

    def _update_method_status(self):
        result = self.results.get(self.current, {})
        recognition = result.get("recognition") or {}
        method = result.get("method") or (recognition.get("method") if isinstance(recognition, dict) else "") or result.get("recognition_method") or ""
        method_text = str(method).lower()
        labels = {
            "reference_motif_matched": "参考辅助匹配 · 参考图形组合已匹配",
            "reference_partial_review": "参考辅助匹配 · 参考组合未齐，必须复核",
            "rules_candidates": "本地规则候选 · 识别尚未验证",
            "unresolved_review": "等待 AI 独立识别或手动复核",
            "vision_selected_source_paths": "AI 判断原始对象 · 需复核",
            "vision_five_components": "五类 AI 组合识别 · 请逐类核对",
            "vision_six_components": "六类 AI 组合识别 · 请逐类核对",
            "manual_six_components": "六类手工复核 · 尚未自动分类",
            "ai_failed_no_selection": "AI 失败 · 未生成选择",
        }
        label = labels.get(method_text)
        if not label:
            if "reference" in method_text or "template" in method_text:
                label = "参考辅助匹配"
            elif "ai" in method_text or "vision" in method_text:
                label = "AI 识别"
            else:
                label = "本地候选识别"
        reference = result.get("reference_match")
        detail = ""
        if result.get('extraction_mode') == 'components':
            missing=[name for name,ids in result.get('component_categories',{}).items() if not ids]
            detail=' · 未识别：'+('、'.join(missing) if missing else '无空类别')
        if isinstance(reference, dict):
            name = reference.get("reference_name") or "未命名参考"
            matched, required = reference.get("matched_count", "?"), reference.get("required_count", "?")
            transfer = reference.get("transfer") or []
            transfer_text = " / ".join(map(str, transfer)) if isinstance(transfer, list) else str(transfer)
            detail = f" · {name}：{matched}/{required}" + (f" · {transfer_text}" if transfer_text else "")
        self.method_status.set(f"{label}{detail} · 候选需复核；导出成功不表示识别已全部正确。")

    def _change_selection(self, include):
        if self.current not in self.results:
            return
        ids = self.objects.selection()
        chosen = self.selections.setdefault(self.current, set())
        chosen.update(ids) if include else chosen.difference_update(ids)
        self._populate_objects(reset_page=False)
        self.status.set("选择已修改；点击“保存修正 SVG / PNG”导出并更新预览。")

    def _toggle_object(self, event):
        oid = self.objects.identify_row(event.y)
        if not oid or self.current not in self.results:
            return
        chosen = self.selections.setdefault(self.current, set())
        chosen.remove(oid) if oid in chosen else chosen.add(oid)
        self._populate_objects(reset_page=False)
        self.status.set("选择已修改，尚未保存。")

    def _save(self):
        if self.busy or self.current not in self.results:
            return
        item = self.current
        ids = sorted(self.selections.get(item, set()))
        if not ids:
            messagebox.showinfo("没有保留对象", "请至少补选一个对象后再保存。")
            return
        result = self.results[item]
        category_name = None
        if result.get('extraction_mode') == 'components':
            if not self._active_group or self._active_group[0] != item:
                messagebox.showinfo('选择类别', '请先在候选下拉框切换到要修正的类别。')
                return
            category_name=result['views'][self._active_group[1]].get('category')
        self._set_busy(True)
        self.status.set("正在保存修正结果…")
        threading.Thread(target=self._export_worker, args=(item, result, ids, category_name), daemon=True).start()

    def _export_worker(self, item, result, ids, category_name=None):
        try:
            from engine import export_selection
            updated = export_selection(result, ids, category_name=category_name)
            self.events.put(("result", item, updated))
            self.events.put(("log", f"修正结果已保存：{updated.get("result_svg")}"))
            self.events.put(("exported", item, updated))
        except Exception as exc:
            self.events.put(("error", f"保存失败：{exc}"))
        finally:
            self.events.put(("done", "保存操作结束，请查看日志及结果预览。"))

    def _load_previews(self, result, *, result_only=False):
        if not result and self.current in self.sources:
            source = self.sources[self.current]
            try:
                result = {'source':str(source), 'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
            except OSError as exc:
                self._append_log(f"原文件无法读取：{exc}")
        if not result_only:
            self.preview_images.clear()
        vectors = {}
        source_key = (result.get('source'), result.get('source_sha256'))
        if source_key != self._source_preview_key:
            self._source_preview_key = source_key
            self._source_vector = None
        if result.get('source') and result.get('source_sha256'):
            try:
                from geometry import svg_document
                if self._source_vector is None:
                    from core.fs_scene import source_preview_pages
                    pages = []
                    for page in source_preview_pages(result):
                        number = page['name'].split()[-1]
                        suffix = '（读取失败，详见日志）' if page['error'] else '（空白）' if not page['objects'] else ''
                        label = f'第 {number} 页{suffix}'
                        pages.append(dict(page, label=label, image=VectorImage(
                            svg_document(page['objects'], background='#ffffff'))))
                        if page['error']:
                            self._append_log(f"{label}：{page['error']}")
                    self._source_vector = VectorDocument(pages)
                vectors['preview_png'] = self._source_vector
            except Exception as exc:
                self._append_log(f"整车矢量预览无法生成：{exc}")
        svg_path = result.get('result_svg')
        if svg_path and result.get('selected_ids'):
            try:
                vectors['result_png'] = VectorImage(Path(svg_path).read_text(encoding='utf8'))
            except Exception as exc:
                self._append_log(f"结果矢量预览无法读取：{exc}")
        for key in self.canvases:
            if result_only and key != 'result_png':
                continue
            path = result.get(key)
            if path:
                try:
                    with Image.open(path) as img:
                        self.preview_images[key] = img.convert("RGBA")
                except (OSError, ValueError) as exc:
                    self._append_log(f"预览无法读取：{path}（{exc}）")
            self.preview_viewers[key].set_image(
                self.preview_images.get(key), vectors.get(key),
                vector_label=("矢量 · 全部文档页面；可选单页放大、切换内嵌原图对照（特殊效果仍需核对）"
                              if key == 'preview_png' else "矢量 · 当前提取结果 SVG"),
                raster_label=("位图 · FS 内嵌原图，清晰度受文件内缩略图限制"
                              if key == 'preview_png' else "位图 · 候选编号预览" if key == 'candidate_png'
                              else "位图 · 提取结果预览"))

        from geometry import union_bounds
        ids = set(map(str, result.get('selected_ids', [])))
        selected = [obj for obj in result.get('objects', [])
                    if str(obj['id']) in ids and obj.get('bbox')]
        page = next((page for page in self._source_vector.pages if page['name'] == 'Page 1'), None) \
            if self._source_vector is not None else None
        self.source_locator.set_source(page, union_bounds(selected) if selected else None)

    def _append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", str(text) + "\n")
        if int(self.log.index("end-1c").split(".")[0]) > 3000:
            self.log.delete("1.0", "500.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _drain(self):
        for _ in range(100):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind, *args = event
            if kind == "log":
                self._append_log(args[0])
            elif kind == "running":
                item, name = args
                self.tasks.set(item, "status", "处理中")
                self.status.set(f"正在处理：{name}")
                self._append_log(f"开始：{self.sources[item]}")
            elif kind == "result":
                item, result = args
                self.results[item] = result
                self.selections[item] = set(map(str, result.get("selected_ids", [])))
                status = {"ready": "候选已导出·需复核", "review": "候选需复核", "failed": "失败"}.get(result.get("status"), "待复核")
                self.tasks.set(item, "status", status)
                self._append_log(f"{self.sources[item].name}：{status}")
                for warning in result.get("warnings", []):
                    self._append_log(f"提示：{warning}")
                if self.current is None:
                    self.tasks.selection_set(item)
                    self.current = item
                if self.current == item:
                    self._populate_objects()
                    self._load_previews(result)
                    self._update_groups()
            elif kind == "failed":
                item, error = args
                self.tasks.set(item, "status", "失败")
                self._append_log(f"{self.sources[item].name} 失败：{error}")
            elif kind == "step":
                self.progress.configure(value=min(float(self.progress["maximum"]), float(self.progress["value"]) + 1))
            elif kind == "error":
                self._append_log(args[0])
                messagebox.showerror("处理失败", args[0])
            elif kind == "done":
                self._set_busy(False)
                self.status.set(args[0])
        self.root.after(100, self._drain)

    def _close(self):
        if self.busy:
            self.stop_event.set()
            self.status.set("正在处理文件，请等待完成后关闭；已停止后续任务。")
            return
        if self._filter_after is not None:
            self.root.after_cancel(self._filter_after)
        self.root.destroy()


def main():
    root = tk.Tk()
    LiveryApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
