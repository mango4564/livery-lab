"""SVG output uses source Beziers; raster images are previews of those paths."""
from __future__ import annotations

import html
import math
from pathlib import Path
import re

import fitz
from PIL import Image, ImageDraw, ImageFont

IDENTITY = [1, 0, 0, 1, 0, 0]


def union_bounds(objects):
    bounds = [o['bbox'] for o in objects if o.get('bbox')]
    if not bounds:
        return [0, 0, 100, 50]
    return [min(b[0] for b in bounds), min(b[1] for b in bounds),
            max(b[2] for b in bounds), max(b[3] for b in bounds)]


def transform_bounds(bounds, matrix):
    a, b, c, d, e, f = matrix
    pts = [(a*x+c*y+e, b*x+d*y+f)
           for x in (bounds[0], bounds[2]) for y in (bounds[1], bounds[3])]
    return [min(p[0] for p in pts), min(p[1] for p in pts),
            max(p[0] for p in pts), max(p[1] for p in pts)]


def svg_document(objects, bounds=None, *, width=1600, margin=0.015,
                 y_up=True, diagnostic=False):
    bounds = list(bounds or union_bounds(objects))
    x0, y0, x1, y1 = bounds
    span = max(x1-x0, y1-y0, 1e-6)
    pad = span * margin
    x0 -= pad; y0 -= pad; x1 += pad; y1 += pad
    w, h = max(x1-x0, 1e-6), max(y1-y0, 1e-6)
    height = max(1, round(width*h/w))
    top = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="{x0:.14g} {y0:.14g} {w:.14g} {h:.14g}">'
    body = [top, '<metadata>Original FS curves; physical scale has not been verified.</metadata>']
    if y_up:
        body.append(f'<g transform="translate(0 {y0+y1:.14g}) scale(1 -1)">')
    for obj in objects:
        if not obj.get('d'):
            continue
        fill = obj.get('fill') or '#8b98a8'
        # Importer only supplies hex colours and the SVG keyword 'none'.
        if not re.fullmatch(r'#[0-9a-fA-F]{6}|none', fill):
            fill = '#8b98a8'
        matrix = obj.get('matrix', IDENTITY)
        if len(matrix) != 6 or not all(math.isfinite(v) for v in matrix):
            raise ValueError('Invalid source transform')
        attributes = [f'id="{html.escape(str(obj["id"]), quote=True)}"',
                      f'd="{html.escape(obj["d"], quote=True)}"',
                      'transform="matrix('+' '.join(f'{v:.14g}' for v in matrix)+')"',
                      f'fill="{fill}"', 'fill-rule="evenodd"']
        stroke = obj.get('stroke', 'none')
        if diagnostic and fill in ('none','#ffffff','#FFFFFF'):
            stroke = '#64748b'
        if re.fullmatch(r'#[0-9a-fA-F]{6}', stroke):
            attributes.extend([f'stroke="{stroke}"', f'stroke-width="{span*0.0005:.14g}"'])
        body.append('<path '+' '.join(attributes)+'/>')
    if y_up:
        body.append('</g>')
    body.append('</svg>')
    return '\n'.join(body)


def rasterize(svg, path, max_width=1800):
    doc = fitz.open(stream=svg.encode('utf-8'), filetype='svg')
    page = doc[0]
    scale = min(max_width / max(page.rect.width, 1), 4000/max(page.rect.height, 1))
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=True)
    pix.save(str(path))
    doc.close()


def candidate_image(objects, path, *, bounds=None, y_up=True, max_labels=150):
    # Geometry-only scene supplies precise correspondence between labels and IDs.
    bounds = list(bounds or union_bounds(objects))
    painted=sorted(objects,key=lambda o:o.get('score',0))
    svg = svg_document(painted, bounds, width=1800, margin=0.015, y_up=y_up, diagnostic=True)
    rasterize(svg, path)
    image = Image.open(path).convert('RGBA')
    bg = Image.new('RGBA', image.size, 'white'); bg.alpha_composite(image)
    draw = ImageDraw.Draw(bg)
    try:
        font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 13)
    except OSError:
        font = ImageFont.load_default()
    x0, y0, x1, y1 = bounds
    pad = max(x1-x0, y1-y0, 1e-6)*0.015
    x0-=pad; y0-=pad; x1+=pad; y1+=pad
    ranked = sorted(objects, key=lambda o:o.get('score',0), reverse=True)[:max_labels]
    for obj in ranked:
        b = obj['bbox']
        left=(b[0]-x0)/(x1-x0)*bg.width
        right=(b[2]-x0)/(x1-x0)*bg.width
        top=((y1-b[3]) if y_up else (b[1]-y0))/(y1-y0)*bg.height
        bottom=((y1-b[1]) if y_up else (b[3]-y0))/(y1-y0)*bg.height
        colour='#d97706' if obj.get('score',0)<0.7 else '#dc2626'
        draw.rectangle([left,top,right,bottom],outline=colour,width=2)
        label=str(obj['id'])
        tx=max(0,min(left,bg.width-len(label)*8)); ty=max(0,top-17)
        bb=draw.textbbox((tx,ty),label,font=font)
        draw.rectangle(bb,fill='white')
        draw.text((tx,ty),label,fill='#991b1b',font=font)
    bg.convert('RGB').save(path)


def alpha_normalized(path, size=(1600,500)):
    """Benchmark helper: ignore canvas padding, preserve aspect ratio."""
    im=Image.open(path).convert('RGBA')
    box=im.getchannel('A').getbbox()
    if box is None:
        return Image.new('L',size)
    im=im.getchannel('A').crop(box)
    im.thumbnail(size,Image.Resampling.LANCZOS)
    out=Image.new('L',size)
    out.paste(im,((size[0]-im.width)//2,(size[1]-im.height)//2))
    return out


def candidate_sheet(objects, path, columns=5):
    """Render the exact AI candidate pool as labelled original-curve thumbnails.

    No per-object files are written. The source path data is passed through
    svg_document unchanged; fixed SVG viewport dimensions keep even very tall
    paths from allocating an enormous intermediate page. Unknown display fills
    are explicitly diagnostic and never claimed to be original colours.
    """
    objects = list(objects)
    if len(objects) > 150:
        raise ValueError('candidate_sheet accepts at most 150 candidates; pass the exact AI candidate pool')
    if not isinstance(columns, int) or isinstance(columns, bool) or not 1 <= columns <= 10:
        raise ValueError('columns must be an integer from 1 to 10')
    tile_width, image_height, label_height = 320, 140, 30
    rows = max(1, math.ceil(len(objects) / columns))
    if rows * (image_height + label_height) > 5100:
        raise ValueError('candidate sheet would exceed 5100 pixels high; increase columns')
    sheet = Image.new('RGB', (columns * tile_width, rows * (image_height + label_height)), '#f5f6f8')
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 15)
        small_font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 12)
    except OSError:
        font = small_font = ImageFont.load_default()
    if not objects:
        draw.text((18, 55), 'No candidates in the AI pool.', fill='#475569', font=font)
    for index, obj in enumerate(objects):
        left = index % columns * tile_width
        top = index // columns * (image_height + label_height)
        tile = Image.new('RGBA', (tile_width, image_height), '#ffffff')
        tile_draw = ImageDraw.Draw(tile)
        for y in range(0, image_height, 12):
            for x in range(0, tile_width, 12):
                if (x // 12 + y // 12) % 2:
                    tile_draw.rectangle((x, y, x + 11, y + 11), fill='#dce1e8')
        fill = obj.get('fill')
        known_fill = isinstance(fill, str) and re.fullmatch(r'#[0-9a-fA-F]{6}|none', fill) is not None
        label_fill = fill if known_fill else 'UNKNOWN (diagnostic)'
        try:
            if not obj.get('d'):
                raise ValueError('missing source path')
            bounds = obj.get('bbox')
            if not isinstance(bounds, (tuple, list)) or len(bounds) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in bounds):
                raise ValueError('invalid bounds')
            if bounds[2] < bounds[0] or bounds[3] < bounds[1]:
                raise ValueError('reversed bounds')
            # Preserve source shape proportions through viewBox's default meet;
            # replace the intrinsic height BEFORE fitz ever opens the SVG.
            svg = svg_document([obj], bounds=bounds, width=tile_width - 20, margin=0.025, diagnostic=False)
            svg = re.sub(r'(<svg\b[^>]*\bheight=")[^"]*', r'\g<1>' + str(image_height - 16), svg, count=1)
            with fitz.open(stream=svg.encode('utf-8'), filetype='svg') as document:
                page = document[0]
                pix = page.get_pixmap(alpha=True)
                rendered = Image.frombytes('RGBA', (pix.width, pix.height), pix.samples)
            # Retain aspect ratio if a renderer rounds CSS dimensions.
            rendered.thumbnail((tile_width - 20, image_height - 16), Image.Resampling.LANCZOS)
            tile.alpha_composite(rendered, ((tile_width - rendered.width) // 2, (image_height - rendered.height) // 2))
        except Exception as exc:
            # A malformed/unsupported object must not hide all other candidates.
            tile_draw.rectangle((8, 40, tile_width - 8, 92), fill='#fff1f2')
            tile_draw.text((14, 46), 'RENDER FAILED', fill='#b91c1c', font=font)
            error = type(exc).__name__
            tile_draw.text((14, 69), error, fill='#b91c1c', font=small_font)
        sheet.paste(tile.convert('RGB'), (left, top))
        draw.rectangle((left, top, left + tile_width - 1, top + image_height + label_height - 1), outline='#94a3b8')
        label = f'{obj.get("id", "NO-ID")} | {label_fill}'
        # Avoid bleeding into neighbouring labels, preserving the ID first.
        while draw.textlength(label, font=font) > tile_width - 16 and len(label) > 3:
            label = label[:-4] + '...'
        draw.text((left + 8, top + image_height + 6), label, fill='#0f172a', font=font)
        if not known_fill:
            draw.text((left + 8, top + 5), 'UNKNOWN FILL: DIAGNOSTIC', fill='#9a3412', font=small_font)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, format='PNG')
    return path
