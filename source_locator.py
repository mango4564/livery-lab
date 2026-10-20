"""Render a source-page thumbnail and map extraction bounds into it."""
import math
import tkinter as tk
from tkinter import ttk
import xml.etree.ElementTree as ET

from PIL import Image, ImageChops, ImageDraw, ImageTk
from geometry import svg_document
from vector_preview import VectorImage


def source_rectangle(viewbox, bounds, size, viewport=None):
    """Match SVG's centered xMidYMid meet and the source Y-up transform."""
    x, y, width, height = viewbox
    vw, vh = viewport or (width, height)
    scale = min(size[0] / vw, size[1] / vh) * min(vw / width, vh / height)
    ox = (size[0] - width * scale) / 2
    oy = (size[1] - height * scale) / 2
    x0, y0, x1, y1 = bounds
    return (ox + (x0 - x) * scale, oy + (y + height - y1) * scale,
            ox + (x1 - x) * scale, oy + (y + height - y0) * scale)


def visible_source_bounds(vector, viewbox):
    """Find painted content rather than oversized white/invisible geometry.

    The bounded probe only discovers a viewport. The final thumbnail is rendered
    again from vectors, so cropping does not magnify low-resolution pixels.
    """
    scale = min(2048 / vector.width, 2048 / vector.height)
    size = (max(1, math.ceil(vector.width * scale)),
            max(1, math.ceil(vector.height * scale)))
    # Use the same centered fit for detection and coordinate inversion.
    scale = min(size[0] / vector.width, size[1] / vector.height)
    offset = ((size[0] - vector.width * scale) / 2,
              (size[1] - vector.height * scale) / 2)
    tile, _, _ = vector.render(scale, offset, size)
    white = Image.new('RGBA', size, 'white')
    white.alpha_composite(tile)
    diff = ImageChops.difference(white.convert('RGB'), Image.new('RGB', size, 'white'))
    r, g, b = diff.split()
    mask = ImageChops.lighter(ImageChops.lighter(r, g), b).point(
        [0 if value < 8 else 255 for value in range(256)])
    box = mask.getbbox()
    if box is None:
        return None
    # A remote speck or a thin editing mark can otherwise keep an entire blank
    # sheet in view. Trim sparse outer rows/columns relative to the main drawing.
    def occupied_span(bitmap):
        counts = [bitmap.crop((0, row, bitmap.width, row+1)).histogram()[255]
                  for row in range(bitmap.height)]
        peak = max(counts)
        threshold = max(2, peak * 0.015) if peak > 2 else 1
        occupied = [i for i, count in enumerate(counts) if count >= threshold]
        if not occupied:
            return None
        bands = [[occupied[0]]]
        gap = max(8, bitmap.height * 0.03)
        for i in occupied[1:]:
            if i - bands[-1][-1] > gap:
                bands.append([])
            bands[-1].append(i)
        weights = [sum(counts[i] for i in band) for band in bands]
        # Ignore isolated, small notes far from the design; retain substantial
        # separate views even when there is whitespace between them.
        kept = [band for band, weight in zip(bands, weights) if weight >= max(weights)*0.05]
        return kept[0][0], kept[-1][-1]+1

    rows = occupied_span(mask)
    columns = occupied_span(mask.transpose(Image.Transpose.ROTATE_90))
    if rows and columns:
        # Rotation reverses the column order.
        box = (mask.width-columns[1], rows[0], mask.width-columns[0], rows[1])
    x, y, width, height = viewbox
    left, top, right, bottom = source_rectangle(viewbox, (x, y, x+width, y+height),
                                                size, (vector.width, vector.height))
    unit = (right - left) / width
    return (x + (box[0]-1-left) / unit, y+height - (box[3]+1-top) / unit,
            x + (box[2]+1-left) / unit, y+height - (box[1]-1-top) / unit)


class SourceLocatorRenderer:
    """Pure image preparation; usable by the preview worker without Tk calls."""
    def __init__(self):
        self._key = None
        self._base = None
        self._viewbox = None
        self._content_bounds = None
        self._crop_bounds = None
        self._viewport = None

    def prepare(self, page, bounds):
        size = (216, 124)
        if page is None or page.get('error') or not page.get('objects'):
            return None
        key = page['image']
        if key != self._key:
            svg = svg_document(page['objects'], background='#ffffff')
            viewbox = tuple(map(float, ET.fromstring(svg).get('viewBox').split()))
            self._content_bounds = visible_source_bounds(page['image'], viewbox)
            self._key = key
            self._crop_bounds = None
            self._base = None
        # Keep selected white/pale objects or distant candidates in view too.
        valid_bounds = bounds if bounds and all(math.isfinite(v) for v in bounds) else None
        regions = [region for region in (self._content_bounds, valid_bounds) if region]
        crop = ((min(b[0] for b in regions), min(b[1] for b in regions),
                 max(b[2] for b in regions), max(b[3] for b in regions)) if regions else None)
        if self._base is None or crop != self._crop_bounds:
            svg = svg_document(page['objects'], bounds=crop, margin=0.035, background='#ffffff')
            self._viewbox = tuple(map(float, ET.fromstring(svg).get('viewBox').split()))
            vector = VectorImage(svg)
            self._viewport = (vector.width, vector.height)
            scale = min(size[0] / vector.width, size[1] / vector.height)
            offset = ((size[0] - vector.width * scale) / 2,
                      (size[1] - vector.height * scale) / 2)
            tile, _, _ = vector.render(scale, offset, size)
            self._base = Image.new('RGBA', size, 'white')
            self._base.alpha_composite(tile)
            self._crop_bounds = crop
        image = self._base.copy()
        if bounds and all(math.isfinite(v) for v in bounds):
            box = source_rectangle(self._viewbox, bounds, size, self._viewport)
            # Keep very small selections visible, without changing their center.
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            hw, hh = max(3, (box[2] - box[0]) / 2), max(3, (box[3] - box[1]) / 2)
            clipped = (max(1, cx-hw), max(1, cy-hh), min(214, cx+hw), min(122, cy+hh))
            if clipped[2] >= clipped[0] and clipped[3] >= clipped[1]:
                ImageDraw.Draw(image).rectangle(clipped, outline='#ef3038', width=2)
        return image


class SourceLocator(ttk.Frame):
    def __init__(self, parent, *, title='原稿位置 · 第 1 页（自动裁白）'):
        super().__init__(parent, padding=3, relief='solid', borderwidth=1)
        self.heading = ttk.Label(self, text=title)
        self.heading.pack(anchor='w')
        self.canvas = tk.Canvas(self, width=216, height=124,
                                background='white', highlightthickness=0)
        self.canvas.pack()
        self._renderer = SourceLocatorRenderer()
        self.photo = None
        self._viewbox = None
        self._viewport = None

    def set_source(self, page, bounds):
        image = self._renderer.prepare(page, bounds)
        self.show_prepared(image, self._renderer._viewbox, self._renderer._viewport, bounds is None)

    def show_prepared(self, image, viewbox, viewport, empty=False):
        self.canvas.delete('all')
        self._viewbox, self._viewport = viewbox, viewport
        if image is None:
            self.photo = None
            self.canvas.create_text(108, 62, text='原稿定位预览不可用', fill='#777777')
            return
        self.photo = ImageTk.PhotoImage(image, master=self.canvas)
        self.canvas.create_image(0, 0, image=self.photo, anchor='nw')
        if empty:
            self.canvas.create_text(108, 114, text='当前没有提取对象', fill='#777777')
