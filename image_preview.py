"""Zoomable, pannable image preview for the Tk desktop UI."""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk


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
        if allow_expand:
            button = ttk.Button(bar, text="大窗口查看", command=self.expand)
            button.pack(side="left")
            self.controls.append(button)
        self.canvas = tk.Canvas(self, background="#e8e9ed", highlightthickness=0,
                                height=240, cursor="hand2")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._resize)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda e: self._wheel(e, 1))
        self.canvas.bind("<Button-5>", lambda e: self._wheel(e, -1))
        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._pan)
        self.canvas.bind("<ButtonRelease-1>", self._end_drag)
        self.canvas.bind("<Double-Button-1>", lambda e: self.fit())
        ttk.Label(self, text="滚轮缩放 · 按住左键拖动 · 双击适应窗口",
                  foreground="#666666").pack(anchor="w")
        self.set_image(None)

    def set_image(self, image):
        self.image = image
        for button in self.controls:
            button.configure(state="normal" if image is not None else "disabled")
        self.fit()

    def fit(self):
        self.fitting = True
        if self.image is not None:
            w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
            self.scale = min(max(1, w - 24) / self.image.width,
                             max(1, h - 24) / self.image.height)
            self.offset = ((w - self.image.width * self.scale) / 2,
                           (h - self.image.height * self.scale) / 2)
        self.draw()

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
        self.draw()

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
        self.draw()

    def _end_drag(self, event):
        self._drag = None
        self.canvas.configure(cursor="hand2")

    def _resize(self, event):
        previous = self._size
        self._size = (event.width, event.height)
        if self.fitting:
            self.fit()
        else:
            if previous:
                ox, oy = self.offset
                self.offset = (ox + (event.width - previous[0]) / 2,
                               oy + (event.height - previous[1]) / 2)
            self.draw()

    def draw(self):
        canvas = self.canvas
        canvas.delete("all")
        self.photo = None
        w, h = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
        if self.image is None:
            self.percent.configure(text="")
            canvas.create_text(w / 2, h / 2, text="暂无预览", fill="#777777")
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
        tile = self.image.transform(size, Image.Transform.AFFINE,
                                    (1 / self.scale, 0, (left - ox) / self.scale,
                                     0, 1 / self.scale, (top - oy) / self.scale),
                                    Image.Resampling.BICUBIC)
        background = Image.new("RGBA", size, "white")
        draw = ImageDraw.Draw(background)
        for y in range(0, size[1], 16):
            for x in range(0, size[0], 16):
                if (x // 16 + y // 16) % 2:
                    draw.rectangle((x, y, x + 15, y + 15), fill="#dddddd")
        background.alpha_composite(tile)
        self.photo = ImageTk.PhotoImage(background, master=canvas)
        canvas.create_image(left, top, image=self.photo, anchor="nw")

    def expand(self):
        if self.image is None:
            return
        window = tk.Toplevel(self)
        window.title("图片预览 · 滚轮缩放 / 拖动查看")
        window.geometry("1100x780")
        window.minsize(600, 400)
        viewer = ImagePreview(window, allow_expand=False)
        viewer.pack(fill="both", expand=True)
        viewer.set_image(self.image)
        window.bind("<Escape>", lambda e: window.destroy())
