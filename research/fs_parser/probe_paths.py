"""Research probe for APath geometry in inflated 64-bit FS trees.

Uses validated record lengths/counts, but scans rather than resolves the complete
object graph. Output is diagnostic geometry, NOT a faithful document export.
Group transforms, shared brushes and clipping remain unresolved.
"""
import argparse
import json
import math
import struct
from pathlib import Path
from inspect_fs import fields


def decode_path(payload):
    f = fields(payload, word=8)
    vertices, segments = struct.unpack("<II", f[1])
    raw = f[2]
    pos = 0
    commands = []
    points = []
    for index in range(segments):
        x, y, kind = struct.unpack_from("<ddI", raw, pos)
        pos += 20
        if not math.isfinite(x + y) or kind not in (2, 4):
            raise ValueError("unknown path segment")
        points.append((x, y))
        if not index:
            commands.append(f"M{x:.12g},{y:.12g}")
        if kind == 2:
            nx, ny = struct.unpack_from("<dd", raw, pos)
            commands.append(f"L{nx:.12g},{ny:.12g}")
        else:
            c1x, c1y, c2x, c2y = struct.unpack_from("<dddd", raw, pos)
            pos += 32
            nx, ny = struct.unpack_from("<dd", raw, pos)
            points.extend([(c1x, c1y), (c2x, c2y)])
            commands.append(f"C{c1x:.12g},{c1y:.12g} {c2x:.12g},{c2y:.12g} {nx:.12g},{ny:.12g}")
    endpoint = struct.unpack_from("<dd", raw, pos)
    points.append(endpoint)
    if pos + 16 != len(raw) or len(points) != vertices:
        raise ValueError("path count/length mismatch")
    base = fields(f[0], word=8)
    matrix = struct.unpack("<dddddd", base[1])
    return {"segments": segments, "vertices": vertices, "flags": list(f[3]),
            "d": " ".join(commands), "own_matrix": matrix,
            "bounds_local": [min(x for x, y in points), min(y for x, y in points),
                             max(x for x, y in points), max(y for x, y in points)]}


def scan(data):
    paths = []
    marker = b"\x0c\0\0\0"
    pos = 0
    while True:
        pos = data.find(marker, pos)
        if pos < 0:
            return paths
        try:
            size = struct.unpack_from("<Q", data, pos + 4)[0]
            if 30 < size <= len(data) - pos - 12:
                record = decode_path(data[pos + 12:pos + 12 + size])
                record.update({"offset": pos, "byte_length": size,
                               "object_id_candidate": int.from_bytes(data[pos - 4:pos], "little")})
                paths.append(record)
        except (ValueError, KeyError, struct.error, OverflowError):
            pass
        pos += 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("objects", type=Path)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    paths = scan(args.objects.read_bytes())
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "path_geometry.json").write_text(json.dumps(paths, indent=2), encoding="utf8")
    bounds = [p["bounds_local"] for p in paths]
    x0, y0 = min(b[0] for b in bounds), min(b[1] for b in bounds)
    x1, y1 = max(b[2] for b in bounds), max(b[3] for b in bounds)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0} {y0} {x1-x0} {y1-y0}">',
           '<!-- Diagnostic only: no group transforms, fill brushes, clipping or closure flags. -->',
           '<g fill="none" stroke="#333" stroke-width="0.6">']
    svg.extend(f'<path id="probe-{i}" d="{p["d"]}"/>' for i, p in enumerate(paths))
    svg.extend(['</g>', '</svg>'])
    (args.out / "geometry_diagnostic.svg").write_text("\n".join(svg), encoding="utf8")
    print(json.dumps({"validated_APath_records": len(paths), "status": "geometry probe only"}))
