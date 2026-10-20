"""Zoomable, pannable image preview for the Tk desktop UI."""
from __future__ import annotations

import math
import queue
import threading
from functools import lru_cache
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk
from vector_preview import VectorDocument, VectorImage


@lru_cache(maxsize=4)
def _checkerboard(size):
    background = Image.new('RGBA', size, 'white')
    draw = ImageDraw.Draw(background)
    for y in range(0, size[1], 16):
        for x in range(0, size[0], 16):
            if (x // 16 + y // 16) % 2:
                draw.rectangle((x, y, x + 15, y + 15), fill='#dddddd')
    return background


def _render_worker(requests, results):
    """Only native/PIL work here; all Tk operations stay on the UI thread."""
    while True:
        request = requests.get()
        if request is None:
            return
        if request[-1]:
            # While wheel/drag events arrive, the UI transforms the exact cached
            # frame. Avoid competing for the GIL with costly vector replay until
            # the gesture pauses; keep only its newest requested viewport.
            while True:
                try:
                    request = requests.get(timeout=.08)
                    if request is None:
                        return
                except queue.Empty:
                    break
        key, image, scale, offset, size, _ = request
        try:
            rendered = image.render(scale, offset, size)
            value = (key, rendered, None)
        except Exception as exc:
            value = (key, None, exc)
        try:
            results.get_nowait()
        except queue.Empty:
            pass
        results.put_nowait(value)


class ImagePreview(ttk.Frame):
    def __init__(self, parent, *, allow_expand=True):
        super().__init__(parent)
        self.image = None
        self.photo = None
        self.scale = 1.0
        self.offset = (0.0, 0.0)
        self.fitting = True
        self._drag = None
        self._size = None
        self._draw_after = None
        self._draw_key = None
        self._render_requests = queue.Queue(maxsize=1)
        self._render_results = queue.Queue(maxsize=1)
        self._render_thread = None
        self._render_after = None
        self._render_wait_key = None
        self._frame = None
        self._destroyed = False
        self.vector_image = None
        self.raster_image = None
        self.vector_label = "矢量"
        self.raster_label = "位图"
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        self.controls = []
        for label, command in (("－", lambda: self.zoom(1 / 1.25)),
                               ("＋", lambda: self.zoom(1.25)),
                               ("适应窗口", self.fit),
                               ("原始尺寸", lambda: self.set_scale(1.0))):
            button = ttk.Button(bar, text=label, command=command, width=8)
            button.pack(side="left", padx=(0, 4), pady=3)
            self.controls.append(button)
        self.percent = ttk.Label(bar, width=9)
        self.percent.pack(side="left")
        self.source_button = ttk.Button(bar, text="", command=self.toggle_source)
        self.source_label = ttk.Label(self, foreground="#666666", wraplength=650)
        self.page_bar = ttk.Frame(self)
        ttk.Label(self.page_bar, text="页面").pack(side="left")
        self.page_choice = ttk.Combobox(self.page_bar, state='readonly', width=30)
        self.page_choice.pack(side='left', padx=5, pady=3)
        self.page_choice.bind('<<ComboboxSelected>>', lambda e: self.show_page(self.page_choice.current()))
        ttk.Button(self.page_bar, text='上一页', command=lambda: self.show_page(self.page_choice.current()-1)).pack(side='left')
        ttk.Button(self.page_bar, text='下一页', command=lambda: self.show_page(self.page_choice.current()+1)).pack(side='left', padx=4)
        if allow_expand:
            button = ttk.Button(bar, text="大窗口查看", command=self.expand)
            button.pack(side="left")
            self.controls.append(button)
        self.canvas = tk.Canvas(self, background="#e8e9ed", highlightthickness=0,
                                height=240, cursor="hand2")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._resize)
        self.canvas.bind("<Map>", lambda e: self.fit() if self.fitting else self.request_draw())
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda e: self._wheel(e, 1))
        self.canvas.bind("<Button-5>", lambda e: self._wheel(e, -1))
        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._pan)
        self.canvas.bind("<ButtonRelease-1>", self._end_drag)
        self.canvas.bind("<Double-Button-1>", lambda e: self.fit())
        ttk.Label(self, text="滚轮缩放 · 按住左键拖动 · 双击适应窗口",
                  foreground="#666666").pack(anchor="w")
        self.source_label.pack(anchor="w")
        self.set_image(None)

    def set_image(self, image, vector=None, *, vector_label="矢量", raster_label="位图"):
        self._draw_key = None
        self.vector_image, self.raster_image = vector, image
        self.vector_label, self.raster_label = vector_label, raster_label
        self.image = vector if vector is not None else image
        self.page_bar.pack_forget()
        if isinstance(vector, VectorDocument):
            self.page_choice.configure(values=[f'全部页面（共 {len(vector.pages)} 页）'] +
                                       [page['label'] for page in vector.pages])
            self.page_choice.current(0)
            self.page_bar.pack(fill='x', before=self.canvas)
        else:
            self.page_choice.configure(values=[])
        self._update_source()
        for button in self.controls:
            button.configure(state="normal" if self.image is not None else "disabled")
        self.fit()

    def _update_source(self):
        is_vector = isinstance(self.image, VectorImage)
        self.source_label.configure(text=(self.vector_label if is_vector else self.raster_label)
                                    if self.image is not None else "")
        self.source_button.pack_forget()
        if self.vector_image is not None and self.raster_image is not None:
            self.source_button.configure(text=("查看内嵌原图" if "内嵌原图" in self.raster_label else "查看位图")
                                         if is_vector else "查看矢量曲线")
            self.source_button.pack(side="left", padx=4)

    def toggle_source(self):
        if isinstance(self.image, VectorImage):
            self.image = self.raster_image
        else:
            index = self.page_choice.current()
            self.image = (self.vector_image.pages[index-1]['image']
                          if isinstance(self.vector_image, VectorDocument) and index > 0 else self.vector_image)
        self._update_source()
        self.fit()

    def show_page(self, index):
        if not isinstance(self.vector_image, VectorDocument):
            return
        if not 0 <= index <= len(self.vector_image.pages):
            return
        self.page_choice.current(index)
        self.image = self.vector_image.pages[index-1]['image'] if index else self.vector_image
        self._update_source()
        self.fit()

    def fit(self):
        self.fitting = True
        if self.image is not None:
            w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
            self.scale = min(max(1, w - 24) / self.image.width,
                             max(1, h - 24) / self.image.height)
            self.offset = ((w - self.image.width * self.scale) / 2,
                           (h - self.image.height * self.scale) / 2)
        self.request_draw()

    def zoom(self, factor, anchor=None):
        self.set_scale(self.scale * factor, anchor)

    def set_scale(self, scale, anchor=None):
        if self.image is None:
            return
        # Keep tiny fit scales usable while bounding interactive magnification.
        minimum = min(0.01, max(1, self.canvas.winfo_width() - 24) / self.image.width,
                      max(1, self.canvas.winfo_height() - 24) / self.image.height)
        scale = max(minimum, min(32.0, scale))
        ax, ay = anchor or (self.canvas.winfo_width() / 2,
                            self.canvas.winfo_height() / 2)
        ox, oy = self.offset
        ratio = scale / self.scale
        self.offset = (ax - (ax - ox) * ratio, ay - (ay - oy) * ratio)
        self.scale = scale
        self.fitting = False
        self.request_draw()

    def _wheel(self, event, direction=None):
        steps = direction if direction is not None else max(-3, min(3, event.delta / 120))
        self.zoom(1.25 ** steps, (event.x, event.y))
        return "break"

    def _start_drag(self, event):
        self._drag = (event.x, event.y, self.offset)
        self.canvas.configure(cursor="fleur")

    def _pan(self, event):
        if self._drag is None or self.image is None:
            return
        x, y, (ox, oy) = self._drag
        self.offset = (ox + event.x - x, oy + event.y - y)
        self.fitting = False
        self.request_draw()

    def _end_drag(self, event):
        self._drag = None
        self.canvas.configure(cursor="hand2")

    def _resize(self, event):
        previous = self._size
        self._size = (event.width, event.height)
        if previous == self._size:
            return
        if self.fitting:
            self.fit()
        else:
            if previous:
                ox, oy = self.offset
                self.offset = (ox + (event.width - previous[0]) / 2,
                               oy + (event.height - previous[1]) / 2)
            self.request_draw()

    def request_draw(self):
        # One render per frame, including a burst of resize/wheel/motion events.
        if self._draw_after is None:
            self._draw_after = self.after(16, self._flush_draw)

    def _flush_draw(self):
        self._draw_after = None
        if self.canvas.winfo_ismapped():
            self.draw()

    def destroy(self):
        self._destroyed = True
        if self._render_after is not None:
            self.after_cancel(self._render_after)
            self._render_after = None
        if self._render_thread is not None:
            try:
                self._render_requests.get_nowait()
            except queue.Empty:
                pass
            self._render_requests.put_nowait(None)
        if self._draw_after is not None:
            self.after_cancel(self._draw_after)
            self._draw_after = None
        super().destroy()

    def draw(self):
        canvas = self.canvas
        w, h = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
        key = (id(self.image), self.scale, self.offset, w, h)
        if key == self._draw_key:
            return
        self._draw_key = key
        canvas.delete('preview')
        if self.image is None:
            self.photo = None
            self._frame = None
            self.percent.configure(text="")
            canvas.create_text(w / 2, h / 2, text="暂无预览", fill="#777777", tags='preview')
            return
        self.percent.configure(text=f"{self.scale:.1%}")
        ox, oy = self.offset
        left, top = max(0, math.floor(ox)), max(0, math.floor(oy))
        right = min(w, math.ceil(ox + self.image.width * self.scale))
        bottom = min(h, math.ceil(oy + self.image.height * self.scale))
        if right <= left or bottom <= top:
            return
        # Render only the visible region, so even 3200% zoom never creates a
        # giant intermediate bitmap. Coordinates always refer to the source.
        size = (right - left, bottom - top)
        if isinstance(self.image, VectorImage):
            # Transform the last exact frame immediately during interaction.
            # A fresh vector render always replaces it when the worker catches up.
            if self._frame is not None and self._frame[0] is self.image:
                _, old_scale, old_offset, old_tile = self._frame
                ratio = old_scale / self.scale
                tile = old_tile.transform((w, h), Image.Transform.AFFINE,
                    (ratio, 0, old_offset[0] + (0-self.offset[0])*ratio,
                     0, ratio, old_offset[1] + (0-self.offset[1])*ratio),
                    Image.Resampling.BILINEAR)
                self._present(tile, 0, 0)
            request = (key, self.image, self.scale, self.offset, (w, h),
                       self._frame is not None and self._frame[0] is self.image)
            self._render_wait_key = key
            try:
                self._render_requests.get_nowait()
            except queue.Empty:
                pass
            self._render_requests.put_nowait(request)
            if self._render_thread is None:
                self._render_thread = threading.Thread(target=_render_worker,
                    args=(self._render_requests, self._render_results), daemon=True)
                self._render_thread.start()
            if self._render_after is None:
                self._render_after = self.after(16, self._poll_render)
            return
        else:
            tile = self.image.transform(size, Image.Transform.AFFINE,
                                        (1 / self.scale, 0, (left - ox) / self.scale,
                                         0, 1 / self.scale, (top - oy) / self.scale),
                                        Image.Resampling.BICUBIC)
        self._present(tile, left, top)

    def _poll_render(self):
        self._render_after = None
        if self._destroyed:
            return
        try:
            key, rendered, error = self._render_results.get_nowait()
        except queue.Empty:
            self._render_after = self.after(16, self._poll_render)
            return
        if key == self._render_wait_key:
            self._render_wait_key = None
        current = (id(self.image), self.scale, self.offset,
                   max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height()))
        if key == current:
            if error is not None:
                self.canvas.create_text(12, 12, anchor='nw', text=f'矢量预览失败：{error}', tags='preview')
            elif rendered is not None:
                tile, left, top = rendered
                self._frame = (self.image, key[1], key[2], tile)
                self._present(tile, left, top)
        # A newer request may already be running; stale results never repaint.
        if self._render_wait_key is not None:
            self._render_after = self.after(16, self._poll_render)

    def _present(self, tile, left, top):
        background = _checkerboard(tile.size).copy()
        background.alpha_composite(tile)
        self.photo = ImageTk.PhotoImage(background, master=self.canvas)
        self.canvas.delete('preview')
        item = self.canvas.create_image(left, top, image=self.photo, anchor='nw', tags='preview')
        self.canvas.tag_lower(item)

    def expand(self):
        if self.image is None:
            return
        window = tk.Toplevel(self)
        window.title("图片预览 · 滚轮缩放 / 拖动查看")
        window.geometry("1100x780")
        window.minsize(600, 400)
        viewer = ImagePreview(window, allow_expand=False)
        viewer.pack(fill="both", expand=True)
        viewer.set_image(self.raster_image, self.vector_image,
                         vector_label=self.vector_label, raster_label=self.raster_label)
        if isinstance(self.vector_image, VectorDocument):
            viewer.show_page(self.page_choice.current())
        if not isinstance(self.image, VectorImage) and self.vector_image is not None:
            viewer.toggle_source()
        window.bind("<Escape>", lambda e: window.destroy())
