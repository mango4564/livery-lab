"""Read source FS gradient stops and their per-object coordinate frames.

Reference streams always use 32-bit TLVs; object appearances use the document's
word width. No reference artwork or embedded preview colors are consulted.
See research/fs_gradients/FINDINGS.md for confidence and remaining limits.
"""
from __future__ import annotations

import math
import struct
from xml.sax.saxutils import quoteattr

from .fs_attributes import class_names, reference_records, tlv


def display_color(raw):
    """Decode the serialized display RGB triple, keeping the full 16-bit words.

    This is the cached display color, not an ICC conversion or a color-item
    model (RGB/CMYK/spot) conversion. FS stores each channel in a LE WORD and
    the display byte is its high byte; the corpus values have zero low bytes.
    """
    f = tlv(raw)
    if set(f) != {0} or len(f[0]) != 6:
        raise ValueError('unsupported display color record')
    channels = struct.unpack('<3H', f[0])
    rgb = [v >> 8 for v in channels]
    return {'hex': '#' + bytes(rgb).hex(), 'rgb': rgb,
            'raw_channels': list(channels), 'status': 'stored_display_rgb'}


def spot_display_color(record):
    """Return spot display RGB using the native ASpotColorItem tint formula."""
    f = record['fields']
    if len(f.get(1, b'')) != 1:
        raise ValueError('unsupported spot tint encoding')
    tint = f[1][0]
    result = display_color(tlv(f[0])[2])
    result['spot_tint'] = tint
    rgb = [max(0, min(255, int((v - 255) * tint / 100) + 255))
           for v in result['rgb']]
    result.update(rgb=rgb, hex='#' + bytes(rgb).hex(), status='stored_spot_display_rgb')
    return result


def _records(raw):
    if len(raw) < 2:
        raise ValueError('truncated counted record list')
    count = int.from_bytes(raw[:2], 'little')
    pos = 2
    out = []
    for _ in range(count):
        if pos + 12 > len(raw):
            raise ValueError('truncated nested object header')
        oid, cls, size = struct.unpack_from('<III', raw, pos)
        pos += 12
        if pos + size + 2 > len(raw):
            raise ValueError('truncated nested object payload')
        fields = tlv(raw[pos:pos + size])
        pos += size
        if raw[pos:pos + 2] != b'\r\x90':
            raise ValueError('missing nested object sentinel')
        pos += 2
        out.append({'object_id': oid, 'class_id': cls, 'fields': fields})
    if pos != len(raw):
        raise ValueError('nested object trailing bytes')
    return out


def decode_gradient_record(record, classes):
    f = record['fields']
    g = tlv(f[1])
    mode = int.from_bytes(g[5], 'little')
    color_list = tlv(tlv(g[9])[5])[2]
    stops = []
    for stop in _records(color_list):
        if classes.get(stop['class_id']) != 'AGradientColor':
            raise ValueError('unsupported gradient stop class')
        sf = stop['fields']
        offset, = struct.unpack('<d', sf[2])
        if not math.isfinite(offset) or not 0 <= offset <= 1:
            raise ValueError('invalid gradient stop offset')
        color = display_color(sf[1])
        stops.append({'offset': offset, 'color': color['hex'],
                      'object_id': stop['object_id'],
                      'raw_channels': color['raw_channels']})
    if len(stops) < 2 or any(b['offset'] < a['offset'] for a, b in zip(stops, stops[1:])):
        raise ValueError('invalid gradient stop ordering')
    return {'class_id': record['class_id'], 'mode': mode,
            'kind': {1: 'linear', 2: 'radial'}.get(mode, 'unsupported'),
            'flags': int.from_bytes(g.get(7, b''), 'little'),
            'reference_start': list(struct.unpack('<2d', f[2])),
            'reference_end': list(struct.unpack('<2d', f[3])),
            'stops': stops, 'status': 'decoded_source_gradient'}


def decode_gradients(ole):
    """Return {brush_id: gradient}; malformed entries retain an explicit error."""
    path = 'FlexiSIGN Contents/Reference/AGradientBrushItem Data'
    if not ole.exists(path):
        return {}
    classes = class_names(ole)
    result = {}
    for oid, record in reference_records(ole.openstream(path).read()).items():
        try:
            result[oid] = decode_gradient_record(record, classes)
        except (ValueError, KeyError, IndexError, struct.error) as exc:
            result[oid] = {'status': 'unsupported_gradient', 'error': str(exc)}
    return result


def gradient_appearances(raw, classes, word=4):
    """Decode AGradientBrush appearances, including their own page-space frame.

    Use these endpoints, NOT the shared reference endpoints: transformed copies
    and mirrors keep shared stop lists but store different per-object endpoints.
    """
    if raw in (b'', b'\0\0\0\0'):
        return []
    pos, out = 0, []
    while pos < len(raw):
        if raw[pos:] == b'\r\x90':
            break
        header = 8 + word
        if pos + header > len(raw):
            raise ValueError('truncated appearance header')
        oid, cls = struct.unpack_from('<II', raw, pos)
        size = int.from_bytes(raw[pos + 8:pos + header], 'little')
        pos += header
        if pos + size > len(raw):
            raise ValueError('truncated appearance payload')
        body = raw[pos:pos + size]
        pos += size
        if raw[pos:pos + 2] == b'\r\x90':
            pos += 2
        if classes.get(cls) != 'AGradientBrush':
            continue
        f = tlv(body, word=word)
        common = tlv(f[0], word=word)
        base = tlv(common[0], word=word)
        matrix = struct.unpack('<6d', common[1])
        start = struct.unpack('<2d', f[1])
        end = struct.unpack('<2d', f[5])
        if not all(math.isfinite(v) for v in matrix + start + end):
            raise ValueError('nonfinite gradient coordinates')
        out.append({'object_id': oid, 'class_id': cls,
                    'brush_id': int.from_bytes(base[1], 'little'),
                    'owner_id': int.from_bytes(base[2], 'little'),
                    'matrix': list(matrix), 'start': list(start), 'end': list(end),
                    'raw_flags': f.get(6, b'').hex()})
    return out


def gradient_svg(gradient, appearance, identifier):
    """Build an SVG paint-server definition; return None for unsupported modes.

    Source normalized gradient coordinates are mapped through the serialized
    six-double appearance matrix. Geometry itself is already in page space.
    """
    kind = gradient.get('kind')
    if kind not in ('linear', 'radial') or not gradient.get('stops'):
        return None
    x1, y1 = appearance['start']
    x2, y2 = appearance['end']
    fmt = lambda v: format(v, '.17g')
    transform = 'matrix(' + ' '.join(map(fmt, appearance['matrix'])) + ')'
    common = (f'id={quoteattr(str(identifier))} gradientUnits="userSpaceOnUse" '
              f'gradientTransform={quoteattr(transform)} spreadMethod="pad" color-interpolation="sRGB"')
    if kind == 'linear':
        tag = 'linearGradient'
        attrs = f'x1="{fmt(x1)}" y1="{fmt(y1)}" x2="{fmt(x2)}" y2="{fmt(y2)}"'
    else:
        radius = math.hypot(x2 - x1, y2 - y1)
        if radius <= 0:
            return None
        tag = 'radialGradient'
        attrs = f'cx="{fmt(x1)}" cy="{fmt(y1)}" fx="{fmt(x1)}" fy="{fmt(y1)}" r="{fmt(radius)}"'
    stops = ''.join(f'<stop offset="{fmt(s["offset"])}" stop-color={quoteattr(s["color"])}/>'
                    for s in gradient['stops'])
    return f'<{tag} {common} {attrs}>{stops}</{tag}>'
