"""Retain SVG vectors and render only the visible viewport with Skia."""
import math
import re
import threading
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
        self._render_lock = threading.Lock()
        self._picture = None
        if 'data-fs-hairline="true"' in svg:
            self._hairline_tree = ET.fromstring(svg)
            self._hairline_nodes = [node for node in self._hairline_tree.iter()
                                    if node.get('data-fs-hairline') == 'true']
            box = [float(v) for v in self._hairline_tree.get('viewBox', '').replace(',', ' ').split()]
            self._units = min(self.width / box[2], self.height / box[3]) if len(box) == 4 else 1.0
            try:
                self._picture = self._record_vectors(self._hairline_tree)
                self.dom = None
                self._hairline_tree = None
                self._hairline_nodes = []
            except (ValueError, TypeError, IndexError):
                # General SVG (styles, clipping, use, etc.) keeps the importer.
                self._picture = None

    @staticmethod
    def _matrix(value):
        matrix = skia.Matrix()
        for match in re.finditer(r'(matrix|translate|scale)\s*\(([^)]*)\)', value):
            args = [float(v) for v in re.split(r'[\s,]+', match[2].strip())]
            if match[1] == 'matrix' and len(args) == 6:
                a, b, c, d, e, f = args
                part = skia.Matrix.MakeAll(a, c, e, b, d, f, 0, 0, 1)
            elif match[1] == 'translate' and 1 <= len(args) <= 2:
                part = skia.Matrix.Translate(args[0], args[1] if len(args) == 2 else 0)
            elif match[1] == 'scale' and 1 <= len(args) <= 2:
                part = skia.Matrix.Scale(args[0], args[-1])
            else:
                raise ValueError('Unsupported transform')
            matrix = skia.Matrix.Concat(matrix, part)
        if re.sub(r'(matrix|translate|scale)\s*\([^)]*\)', '', value).strip():
            raise ValueError('Unsupported transform')
        return matrix

    @staticmethod
    def _path(value):
        # FS exports absolute commands. Unknown SVG syntax uses SVGDOM instead.
        tokens = re.findall(r'[MLCQHVAZ]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?', value)
        if re.sub(r'[MLCQHVAZ]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?|[\s,]', '', value):
            raise ValueError('Unsupported path')
        path, i, command = skia.Path(), 0, None
        current = start = (0, 0)
        sizes = dict(M=2, L=2, C=6, Q=4, H=1, V=1, A=7)
        while i < len(tokens):
            if tokens[i].isalpha():
                command = tokens[i]
                i += 1
                if command == 'Z':
                    path.close()
                    current = start
                    command = None
                    continue
            count = sizes.get(command)
            if count is None:
                raise ValueError('Invalid path')
            args = list(map(float, tokens[i:i+count]))
            if len(args) != count:
                raise ValueError('Incomplete path')
            i += count
            if command == 'M':
                path.moveTo(*args)
                current = start = tuple(args)
                command = 'L'
            elif command == 'L':
                path.lineTo(*args)
                current = tuple(args)
            elif command == 'C':
                path.cubicTo(*args)
                current = tuple(args[-2:])
            elif command == 'Q':
                path.quadTo(*args)
                current = tuple(args[-2:])
            elif command in ('H', 'V'):
                current = (args[0], current[1]) if command == 'H' else (current[0], args[0])
                path.lineTo(*current)
            else:
                rx, ry, angle, large, sweep, x, y = args
                path.arcTo(rx, ry, angle, skia.Path.kLarge_ArcSize if large else skia.Path.kSmall_ArcSize,
                           skia.PathDirection.kCW if sweep else skia.PathDirection.kCCW, x, y)
                current = (x, y)
        return path

    def _record_vectors(self, root):
        if set(root.attrib) - {'width', 'height', 'viewBox'}:
            raise ValueError('Unsupported SVG root')
        box = [float(v) for v in root.get('viewBox', f'0 0 {self.width} {self.height}').replace(',', ' ').split()]
        unit = min(self.width / box[2], self.height / box[3])
        viewport = skia.Matrix.MakeAll(unit, 0, (self.width-box[2]*unit)/2-box[0]*unit,
                                       0, unit, (self.height-box[3]*unit)/2-box[1]*unit, 0, 0, 1)
        inverse_viewport = skia.Matrix()
        if not viewport.invert(inverse_viewport):
            raise ValueError('Invalid SVG viewport')
        nodes = []
        changed = False
        # Semantic export attributes do not affect drawing. Keep native
        # cosmetic strokes on the same renderer before and after layering.
        group_attributes = {'transform', 'id', 'data-layer',
                            '{http://www.inkscape.org/namespaces/inkscape}groupmode',
                            '{http://www.inkscape.org/namespaces/inkscape}label'}
        by_id = {}
        parents = {child: parent for parent in root.iter() for child in parent}
        for node in root.iter():
            identifier = node.get('id')
            if identifier:
                if identifier in by_id:
                    raise ValueError('Duplicate SVG id')
                by_id[identifier] = node
        def visit(parent, matrix):
            nonlocal changed
            for node in parent:
                tag = node.tag.rsplit('}', 1)[-1]
                if tag in ('metadata', 'defs', 'title'):
                    continue
                if tag == 'g' and not (set(node.attrib) - group_attributes):
                    visit(node, skia.Matrix.Concat(matrix, self._matrix(node.get('transform', ''))))
                    continue
                node_matrix = matrix
                if tag == 'use':
                    xlink = '{http://www.w3.org/1999/xlink}href'
                    if set(node.attrib) - {'href', xlink, 'transform', 'data-layer', 'data-source-id'}:
                        raise ValueError('Unsupported SVG reference')
                    href = node.get('href', node.get(xlink, ''))
                    if (not href.startswith('#') or node.get(xlink, href) != href
                            or href[1:] not in by_id):
                        raise ValueError('Unsupported SVG reference')
                    node_matrix = skia.Matrix.Concat(matrix, self._matrix(node.get('transform', '')))
                    node = by_id[href[1:]]
                    tag = node.tag.rsplit('}', 1)[-1]
                    # Export references point directly to paths. General use
                    # trees, inherited styles and cycles stay with SVGDOM.
                    if tag != 'path':
                        raise ValueError('Unsupported SVG reference target')
                    ancestor = parents.get(node)
                    while ancestor is not None and ancestor is not root:
                        attributes = (group_attributes - {'transform'}
                                      if ancestor.tag.rsplit('}', 1)[-1] == 'g' else {'id'})
                        if set(ancestor.attrib) - attributes:
                            raise ValueError('Unsupported SVG reference ancestry')
                        ancestor = parents.get(ancestor)
                if tag not in ('path', 'rect') or 'style' in node.attrib or 'clip-path' in node.attrib:
                    raise ValueError('Unsupported SVG node')
                # renderNode deliberately omits ancestors; apply their matrices.
                if (tag == 'rect' and not (set(node.attrib) - {'x', 'y', 'width', 'height', 'fill'})
                        and re.fullmatch(r'#[0-9a-fA-F]{6}', node.get('fill', ''))):
                    nodes.append((node, node_matrix, 'rect'))
                    continue
                identifier = node.get('id')
                if not identifier:
                    identifier = f'__preview_node_{len(nodes)}'
                    while identifier in by_id:
                        identifier += '_'
                    node.set('id', identifier)
                    by_id[identifier] = node
                    changed = True
                hairline = node.get('data-fs-hairline') == 'true'
                path = None
                if hairline:
                    allowed = {'id', 'd', 'transform', 'fill', 'fill-rule', 'stroke', 'stroke-width',
                               'vector-effect', 'data-fs-hairline', 'data-layer', 'data-source-id',
                               'data-shared-categories', 'data-paint-editable'}
                    if (tag != 'path' or not re.fullmatch(r'none|#[0-9a-fA-F]{6}', node.get('fill', ''))
                            or set(node.attrib) - allowed):
                        raise ValueError('Unsupported cosmetic stroke')
                    if not re.fullmatch(r'#[0-9a-fA-F]{6}', node.get('stroke', '')):
                        raise ValueError('Unsupported stroke color')
                    path = self._path(node.get('d', ''))
                nodes.append((node, node_matrix, path))
        visit(root, viewport)
        ET.register_namespace('', 'http://www.w3.org/2000/svg')
        dom = self._parse(ET.tostring(root, encoding='unicode')) if changed else self.dom
        recorder = skia.PictureRecorder()
        canvas = recorder.beginRecording(skia.Rect.MakeWH(self.width, self.height), skia.RTreeFactory()())
        pictures = []
        for index, (node, matrix, path) in enumerate(nodes):
            if index and index % 128 == 0:
                pictures.append(recorder.finishRecordingAsPicture())
                recorder = skia.PictureRecorder()
                canvas = recorder.beginRecording(skia.Rect.MakeWH(self.width, self.height), skia.RTreeFactory()())
            canvas.save()
            canvas.concat(matrix)
            if isinstance(path, str):
                color = int(node.get('fill')[1:], 16) | 0xff000000
                canvas.drawRect(skia.Rect.MakeXYWH(*[float(node.get(k, 0)) for k in ('x', 'y', 'width', 'height')]),
                                skia.Paint(Color=color, AntiAlias=True))
            elif path is None:
                # renderNode includes root viewBox, but omits ancestor groups.
                canvas.concat(inverse_viewport)
                dom.renderNode(canvas, node.get('id'))
            else:
                canvas.concat(self._matrix(node.get('transform', '')))
                if node.get('fill') != 'none':
                    path.setFillType(skia.PathFillType.kEvenOdd if node.get('fill-rule') == 'evenodd'
                                     else skia.PathFillType.kWinding)
                    canvas.drawPath(path, skia.Paint(Color=int(node.get('fill')[1:], 16) | 0xff000000,
                                                    AntiAlias=True))
                color = int(node.get('stroke')[1:], 16) | 0xff000000
                canvas.drawPath(path, skia.Paint(Color=color, AntiAlias=True,
                                                Style=skia.Paint.kStroke_Style, StrokeWidth=0))
            canvas.restore()
        pictures.append(recorder.finishRecordingAsPicture())
        return tuple(pictures)

    @staticmethod
    def _parse(svg):
        # MemoryStream's default borrows the Python buffer; own a copy so a
        # temporary UTF-8 bytes object cannot be freed while parsing large SVGs.
        dom = skia.SVGDOM.MakeFromStream(skia.MemoryStream.MakeCopy(svg.encode('utf-8')))
        if dom is None:
            raise ValueError('SVG could not be decoded')
        return dom

    def render(self, scale, offset, viewport):
        # One document can be shared with a worker, locator and expanded view.
        with self._render_lock:
            return self._render(scale, offset, viewport)

    def _render(self, scale, offset, viewport):
        ox, oy = offset
        w, h = map(int, viewport)
        if ox >= w or oy >= h or ox + self.width * scale <= 0 or oy + self.height * scale <= 0:
            return None
        if self._picture is None and self._hairline_nodes and self._hairline_scale != scale:
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
        if self._picture is not None:
            # Bound a native call to a small batch, allowing Tk's Python thread
            # to regain the GIL between batches of a very complex document.
            for picture in self._picture:
                canvas.drawPicture(picture)
        else:
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
