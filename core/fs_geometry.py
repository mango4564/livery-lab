"""Independent FS original-geometry decoder. Does not read reference artwork.

Supports APath line/cubic segments and AEditPath relative control handles.
Object-table discovery is a validated scan, not a resolved scene graph.
"""
import math
import struct
from pathlib import Path

import olefile
from .fs_container import fields, unpack_objects


def partial_fields(data, word):
    pos, result = 1, {}
    while pos + word + 1 <= len(data):
        size = int.from_bytes(data[pos + 1:pos + word + 1], "little")
        if size < word + 1 or pos + size > len(data):
            break
        result[data[pos]] = data[pos + word + 1:pos + size]
        pos += size
    return result, pos


def path_from_nodes(nodes, close):
    x, y = nodes[0][1:3]
    parts = [f"M{x:.17g},{y:.17g}"]
    pairs = list(zip(nodes, nodes[1:]))
    if close:
        pairs.append((nodes[-1], nodes[0]))
    points = [(n[1], n[2]) for n in nodes]
    for a, b in pairs:
        _, ax, ay, aix, aiy, aox, aoy = a
        _, bx, by, bix, biy, box, boy = b
        # The destination node's low flag bit is authoritative. Flexi retains
        # historical nonzero handles on straight segments; using those handles
        # would silently turn genuine lines into curves.
        if b[0] & 1:
            c1, c2 = (ax + aox, ay + aoy), (bx + bix, by + biy)
            points.extend([c1, c2])
            parts.append(f"C{c1[0]:.17g},{c1[1]:.17g} {c2[0]:.17g},{c2[1]:.17g} {bx:.17g},{by:.17g}")
        else:
            parts.append(f"L{bx:.17g},{by:.17g}")
    if close:
        parts.append("Z")
    return " ".join(parts), points


def decode_edit(payload, word):
    top = fields(payload, word=word)
    mixed = top[3]
    base, offset = partial_fields(mixed, word)
    count = struct.unpack_from("<I", mixed, offset)[0]
    if count < 2 or offset + 4 + count * 49 != len(mixed):
        raise ValueError("AEditPath node count mismatch")
    nodes = list(struct.iter_unpack("<Bdddddd", mixed[offset + 4:]))
    if any(not all(math.isfinite(v) and abs(v) < 1e12 for v in n[1:]) for n in nodes):
        raise ValueError("nonfinite or unreasonable node coordinate")
    # Empty field 1 marks closed AEditPath; absence marks an open path.
    closed = 1 in top
    d, points = path_from_nodes(nodes, closed)
    return d, points, base, {"nodes": count, "closed": closed,
                            "closure_evidence": "top-level field 1 presence",
                            "node_flags": sorted(set(n[0] for n in nodes))}


def decode_simple(payload, word):
    f = fields(payload, word=word)
    vertices, count = struct.unpack("<II", f[1])
    if count<1 or count>1_000_000:
        raise ValueError('unsupported path segment count')
    raw, pos, point_count = f[2], 16, 1
    x, y = struct.unpack_from('<dd', raw)
    first = (x, y)
    parts, points = [f'M{x:.17g},{y:.17g}'], [first]
    for _ in range(count):
        kind, = struct.unpack_from('<I', raw, pos)
        pos += 4
        if kind not in (2, 4):
            raise ValueError("unsupported segment kind")
        if kind == 4:
            # HTOPath::appendCurve stores endpoint, control1, control2.
            # Native evidence: research/native_rendering/path_draw_geometry_rea.json.
            nx, ny, c1x, c1y, c2x, c2y = struct.unpack_from('<6d', raw, pos)
            pos += 48
            points.extend([(c1x, c1y), (c2x, c2y)])
            point_count += 3
        else:
            nx, ny = struct.unpack_from('<dd', raw, pos)
            pos += 16
            point_count += 1
        points.append((nx, ny))
        parts.append((f"C{c1x:.17g},{c1y:.17g} {c2x:.17g},{c2y:.17g} " if kind == 4 else "L") + f"{nx:.17g},{ny:.17g}")
    if pos != len(raw) or point_count != vertices:
        raise ValueError("APath geometry length/count mismatch")
    if any(not all(math.isfinite(v) and abs(v)<1e12 for v in point) for point in points):
        raise ValueError('nonfinite or unreasonable path coordinate')
    closed = abs(first[0] - nx) < 1e-5 and abs(first[1] - ny) < 1e-5
    if closed:
        parts.append('Z')
    return " ".join(parts), points, fields(f[0], word=word), {
        "segments": count, "vertices": vertices, 'closed': closed,
        'closure_evidence': 'native serializer endpoint tolerance 1e-5',
        "extra_flags": list(f.get(3, b""))}


def class_names(data):
    start = data.find(b"\x04NULL")
    if start < 0:
        raise ValueError("ClassTable names missing")
    names, pos = [], start
    while pos < len(data):
        n = data[pos]
        name = data[pos + 1:pos + 1 + n]
        if not n or len(name) != n or any(c < 32 or c > 126 for c in name):
            break
        names.append(name.decode("ascii"))
        pos += n + 1
    return names


def decode_oval(payload, word):
    """AOval's parametric width/height in its own AFigure coordinate system.

    Unlike baked APath nodes, these parameters require the stored matrix.
    Emit exact SVG elliptical arcs, including shear/reflection, in page space.
    """
    top = fields(payload, word=word)
    base = fields(top[0], word=word)
    params = fields(top[1], word=word)
    width, height = struct.unpack('<2d', params[1])
    a, b, c, d, e, f = struct.unpack('<6d', base[1])
    if not all(math.isfinite(v) and abs(v) < 1e12
               for v in (width, height, a, b, c, d, e, f)) or min(width, height) <= 0:
        raise ValueError('invalid oval dimensions/matrix')
    rx, ry = width / 2, height / 2
    cx, cy = a * rx + c * ry + e, b * rx + d * ry + f
    ux, uy, vx, vy = a * rx, b * rx, c * ry, d * ry
    xx, xy, yy = ux * ux + vx * vx, ux * uy + vx * vy, uy * uy + vy * vy
    delta = math.hypot(xx - yy, 2 * xy)
    major, minor = math.sqrt(max(0, (xx + yy + delta) / 2)), math.sqrt(max(0, (xx + yy - delta) / 2))
    if minor <= 1e-12:
        raise ValueError('degenerate oval')
    angle = math.degrees(math.atan2(2 * xy, xx - yy) / 2)
    sweep = int(a * d - b * c > 0)
    start, end = (cx + ux, cy + uy), (cx - ux, cy - uy)
    arc = f'A{major:.17g},{minor:.17g} {angle:.17g} 0,{sweep} '
    path = (f'M{start[0]:.17g},{start[1]:.17g} ' + arc + f'{end[0]:.17g},{end[1]:.17g} '
            + arc + f'{start[0]:.17g},{start[1]:.17g} Z')
    dx, dy = math.hypot(ux, vx), math.hypot(uy, vy)
    return path, [(cx - dx, cy - dy), (cx + dx, cy + dy)], base, {
        'closed': True, 'primitive': 'ellipse', 'width': width, 'height': height,
        'geometry_evidence': 'AOval field1/field1 dimensions; AFigure matrix applied once'}


def extract_geometry(source, page='Page 1'):
    source = Path(source)
    with olefile.OleFileIO(source) as ole:
        names = class_names(ole.openstream("FlexiSIGN Contents/Index/ClassTable").read())
        raw, compression = unpack_objects(ole.openstream(f"FlexiSIGN Contents/{page}/Objects").read())
    word = compression["tlv_word_bytes"]
    objects, rejected = [], 0
    for class_name, decoder in [("APath", decode_simple), ("AEditPath", decode_edit),
                                ('AOval', decode_oval)]:
        if class_name not in names:
            continue
        class_index = names.index(class_name)
        marker, pos = struct.pack("<I", class_index), 0
        while True:
            pos = raw.find(marker, pos)
            if pos < 0:
                break
            if pos<4 or pos+4+word>len(raw):
                pos+=1
                continue
            try:
                size = int.from_bytes(raw[pos + 4:pos + 4 + word], "little")
                start = pos + 4 + word
                if not 30 < size <= len(raw) - start:
                    raise ValueError("not a valid wrapper")
                d, points, base, details = decoder(raw[start:start + size], word)
                matrix = list(struct.unpack("<dddddd", base[1]))
                if not all(math.isfinite(v) for v in matrix):
                    raise ValueError('nonfinite authoring matrix')
                obj = {"class_name": class_name, "object_id": int.from_bytes(raw[pos - 4:pos], "little"),
                       "source_offset": pos, "payload_offset": start, "payload_length": size,
                       "d": d, "bbox": [min(x for x, y in points), min(y for x, y in points),
                                          max(x for x, y in points), max(y for x, y in points)],
                       "own_matrix": matrix, "tree_path": None, "details": details,
                       "appearance_hex": base.get(3, b"").hex(),
                       # AFigure fields 2/3 are independent pen/brush attributes;
                       # AObject field 1 preserves the serialized visibility flags.
                       # Native evidence: research/native_rendering/figureser.json
                       # and objectser.json. Decode appearance semantics separately.
                       "pen_hex": base.get(2, b"").hex(),
                       "object_flags": int.from_bytes(fields(base[0], word=word)[1], "little")}
                objects.append(obj)
            except (KeyError, ValueError, struct.error, OverflowError):
                rejected += 1
            pos += 1
    objects.sort(key=lambda o: o["source_offset"])
    for i, obj in enumerate(objects):
        obj["source_order_index"] = i
    return {"source": str(source.resolve()), "objects": objects,
            "geometry_stats": {"objects": len(objects), "APath": sum(o["class_name"] == "APath" for o in objects),
                               "AEditPath": sum(o["class_name"] == "AEditPath" for o in objects),
                               "rejected_marker_candidates": rejected, "tlv_word_bytes": word},
            "warnings": ["Object discovery uses validated wrappers; hierarchy is not resolved.",
                         "Path bbox is the control-point envelope; ellipse bbox uses exact projected radii.",
                         "Parent transforms, colors, visibility, compound operations and clipping are unresolved.",
                         "APath closure uses endpoint tolerance; AEditPath closure uses field 1 presence.",
                         "Only APath, AEditPath and AOval geometry is decoded."]}
