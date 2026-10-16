"""Retain SVG vectors and render only the visible viewport with Skia."""
import math
import xml.etree.ElementTree as ET

import skia
from PIL import Image, ImageDraw, ImageFont


class VectorImage:
    def __init__(self, svg):
        self.dom = self._parse(svg)
        size = self.dom.containerSize()
        self.width, self.height = size.width(), size.height()
        if not (math.isfinite(self.width) and math.isfinite(self.height)
                and self.width > 0 and self.height > 0):
            raise ValueError('Invalid vector viewport')
        self._hairline_tree = None
        self._hairline_nodes = []
        self._hairline_scale = None
        if 'data-fs-hairline="true"' in svg:
            self._hairline_tree = ET.fromstring(svg)
            self._hairline_nodes = [node for node in self._hairline_tree.iter()
                                    if node.get('data-fs-hairline') == 'true']
            box = [float(v) for v in self._hairline_tree.get('viewBox', '').replace(',', ' ').split()]
            self._units = min(self.width / box[2], self.height / box[3]) if len(box) == 4 else 1.0

    @staticmethod
    def _parse(svg):
        # MemoryStream's default borrows the Python buffer; own a copy so a
        # temporary UTF-8 bytes object cannot be freed while parsing large SVGs.
        dom = skia.SVGDOM.MakeFromStream(skia.MemoryStream.MakeCopy(svg.encode('utf-8')))
        if dom is None:
            raise ValueError('SVG could not be decoded')
        return dom

    def render(self, scale, offset, viewport):
        ox, oy = offset
        w, h = map(int, viewport)
        if ox >= w or oy >= h or ox + self.width * scale <= 0 or oy + self.height * scale <= 0:
            return None
        if self._hairline_nodes and self._hairline_scale != scale:
            # The SVG importer ignores non-scaling-stroke. Recompute native
            # cosmetic pen widths only on zoom; panning reuses the parsed DOM.
            for node in self._hairline_nodes:
                node.set('stroke-width', format(1 / (scale * self._units), '.17g'))
            ET.register_namespace('', 'http://www.w3.org/2000/svg')
            self.dom = self._parse(ET.tostring(self._hairline_tree, encoding='unicode'))
            self._hairline_scale = scale
        info = skia.ImageInfo.Make(w, h, skia.kRGBA_8888_ColorType, skia.kPremul_AlphaType)
        surface = skia.Surface.MakeRaster(info)
        if surface is None:
            raise ValueError('Vector viewport cannot be allocated')
        canvas = surface.getCanvas()
        canvas.clear(skia.ColorTRANSPARENT)
        canvas.translate(ox, oy)
        canvas.scale(scale, scale)
        self.dom.render(canvas)
        # Unpremultiply before checkerboard composition for correct edge colors.
        tile = Image.frombytes('RGBa', (w, h), surface.makeImageSnapshot().tobytes()).convert('RGBA')
        return tile, 0, 0


class VectorDocument(VectorImage):
    """A vertical overview retaining a separate vector renderer per page."""
    def __init__(self, pages):
        if not pages:
            raise ValueError('Document has no pages')
        self.pages = pages
        self.width = max(page['image'].width for page in pages) + 48
        self.positions = []
        y = 24
        for page in pages:
            self.positions.append((24, y + 48))
            y += page['image'].height + 96
        self.height = y
        try:
            self.font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 14)
        except OSError:
            self.font = ImageFont.load_default()

    def render(self, scale, offset, viewport):
        w, h = map(int, viewport)
        output = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        for page, (x, y) in zip(self.pages, self.positions):
            px, py = offset[0] + x * scale, offset[1] + y * scale
            image = page['image']
            if py > h or py + image.height * scale < 0:
                continue
            rendered = image.render(scale, (px, py), (w, h))
            if rendered:
                tile, tx, ty = rendered
                output.alpha_composite(tile, (tx, ty))
            heading_y = py - min(30, max(14, 36 * scale))
            if -24 < heading_y < h:
                draw = ImageDraw.Draw(output)
                title = page['label']
                box = draw.textbbox((px, heading_y), title, font=self.font)
                draw.rectangle(box, fill='white')
                draw.text((px, heading_y), title, font=self.font,
                          fill='#b91c1c' if page.get('error') else '#334155')
        return output, 0, 0
