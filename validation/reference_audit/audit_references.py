"""Reference acceptance/label audit only. Never imported by extraction engine."""
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
LAB = HERE.parents[1]
ROOT = LAB.parent
sys.path.insert(0, str(LAB))
from core.fs_geometry import extract_geometry
import core.fs_geometry as decoder_module

FLAG_AUDIT = {"paths_with_stale_handles": 0, "straight_segments_with_stale_handles": 0,
              "parsed_edit_paths": 0}


def flag_only_nodes(nodes, close):
    """Candidate decoder under test; changes no source/core files."""
    FLAG_AUDIT["parsed_edit_paths"] += 1
    parts = [f"M{nodes[0][1]:.17g},{nodes[0][2]:.17g}"]
    points = [(n[1], n[2]) for n in nodes]
    pairs = list(zip(nodes, nodes[1:]))
    if close:
        pairs.append((nodes[-1], nodes[0]))
    stale = 0
    for a, b in pairs:
        _, ax, ay, aix, aiy, aox, aoy = a
        _, bx, by, bix, biy, box, boy = b
        if b[0] & 1:
            c1, c2 = (ax + aox, ay + aoy), (bx + bix, by + biy)
            points.extend([c1, c2])
            parts.append(f"C{c1[0]:.17g},{c1[1]:.17g} {c2[0]:.17g},{c2[1]:.17g} {bx:.17g},{by:.17g}")
        else:
            stale += bool(aox or aoy or bix or biy)
            parts.append(f"L{bx:.17g},{by:.17g}")
    if close:
        parts.append("Z")
    FLAG_AUDIT["paths_with_stale_handles"] += bool(stale)
    FLAG_AUDIT["straight_segments_with_stale_handles"] += stale
    return " ".join(parts), points

NUM = r"[-+]?(?:\d*\.)?\d+(?:[eE][-+]?\d+)?"


def tokens(d):
    return tuple(re.findall("[MLCZ]", d)), [float(x) for x in re.findall(NUM, d)]


def canonical_tokens(d):
    """Replace a geometrically straight monotone cubic with the same line.

    References sometimes export zero/collinear handle cubics as L; this changes
    curve parameterization but preserves exactly the geometric locus.
    """
    command, nums = tokens(d)
    out_commands, out_nums, offset, start = [], [], 0, None
    simplified = 0
    for c in command:
        count = {"M": 2, "L": 2, "C": 6, "Z": 0}[c]
        values = nums[offset:offset + count]
        offset += count
        if c == "C" and start:
            end = values[-2:]
            dx, dy = end[0] - start[0], end[1] - start[1]
            norm = dx * dx + dy * dy
            controls = [values[:2], values[2:4]]
            if norm > 1e-20 and all(abs((x - start[0]) * dy - (y - start[1]) * dx) <= 1e-8 * norm**0.5
                                     and -1e-10 <= ((x - start[0]) * dx + (y - start[1]) * dy) / norm <= 1 + 1e-10
                                     for x, y in controls):
                c, values = "L", end
                simplified += 1
        out_commands.append(c)
        out_nums.extend(values)
        if c != "Z":
            start = values[-2:]
    return tuple(out_commands), out_nums, simplified


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    reports, panels = [], []
    candidate = "--flag-only" in sys.argv
    if candidate:
        decoder_module.path_from_nodes = flag_only_nodes
    for ref in sorted((ROOT / "全车矢量图素材/导出彩条参考").glob("*.svg")):
        source = ROOT / "全车矢量图素材" / (ref.stem + ".fs")
        source_sha = sha(source)
        previous_paths = list((LAB / "output").glob(ref.stem + "_*/manifest.json"))
        previous = json.loads(previous_paths[0].read_text(encoding="utf8")) if previous_paths else {}
        for key in ("selected_ids", "suggested_ids"):
            previous[key] = [int(v) for v in previous.get(key, [])]
        if previous and previous["source_sha256"] != source_sha:
            raise ValueError("source digest mismatch: " + source.name)
        fresh_decode_error = None
        before_flag_stats = FLAG_AUDIT.copy()
        try:
            decoded = extract_geometry(source)
        except ValueError as exc:
            if not previous:
                raise
            fresh_decode_error = str(exc)
            decoded = {"objects": previous["objects"], "geometry_stats": previous.get("geometry_stats", {})}
        objects = decoded["objects"]
        previous_objects = {o["object_id"]: o for o in previous.get("objects", [])}
        index = {}
        for obj in objects:
            command, nums, simplified = canonical_tokens(obj["d"])
            index.setdefault((command, len(nums)), []).append((obj, nums, simplified))
        xml = ET.parse(ref).getroot()
        metadata = next((json.loads(e.text) for e in xml if e.tag.endswith("metadata") and e.text), {})
        result = {"source": str(source), "reference": str(ref), "source_sha": source_sha,
                  "reference_sha": sha(ref), "reference_source_sha_matches": metadata.get("source_sha256") == source_sha,
                  "matching_method": "independent command sequence and every coordinate; no object-ID hints used",
                  "fresh_decode_error": fresh_decode_error,
                  "candidate_decoder": "next-node bit0 only" if candidate else "current core",
                  "stale_handle_statistics": {k: FLAG_AUDIT[k] - before_flag_stats[k] for k in FLAG_AUDIT},
                  "geometry_input": "fresh core decode" if fresh_decode_error is None else "digest-verified prior manifest",
                  "positives": [], "unmatched": [], "previous_selected_ids": previous.get("selected_ids", []),
                  "previous_suggested_ids": previous.get("suggested_ids", []), "geometry_stats": decoded["geometry_stats"]}
        reference_paths = []
        compound_groups = []
        for element in [e for e in xml.iter() if e.tag.endswith("path")]:
            subpaths = re.findall(r"M[^M]+", element.attrib["d"])
            if len(subpaths) > 1:
                compound_groups.append({"reference_parent_id_hint": element.attrib.get("id"),
                                        "required_subpaths": len(subpaths), "fill_rule": element.attrib.get("fill-rule", "nonzero")})
                for i, d in enumerate(subpaths):
                    attrs = dict(element.attrib, d=d, reference_parent_id=element.attrib.get("id", ""), subpath_index=str(i))
                    reference_paths.append(ET.Element(element.tag, attrs))
            else:
                reference_paths.append(element)
        result["compound_groups"] = compound_groups
        for path in reference_paths:
            command, expected, reference_simplified = canonical_tokens(path.attrib["d"])
            matches = []
            for obj, actual, source_simplified in index.get((command, len(expected)), []):
                error = max(abs(a - b) for a, b in zip(actual, expected))
                if error <= 1e-8:
                    old = previous_objects.get(obj["object_id"], {})
                    exact_cmd, exact_nums = tokens(obj["d"])
                    ref_cmd, ref_nums = tokens(path.attrib["d"])
                    strict_match = exact_cmd == ref_cmd and len(exact_nums) == len(ref_nums)
                    strict_error = max(abs(a - b) for a, b in zip(exact_nums, ref_nums)) if strict_match else None
                    matches.append({"object_id": obj["object_id"], "source_offset": obj["source_offset"],
                                    "max_coordinate_error": error, "coordinate_count": len(actual),
                                    "transform": "identity raw FS geometry", "bbox": obj["bbox"],
                                    "source_collinear_cubics_normalized": source_simplified,
                                    "reference_collinear_cubics_normalized": reference_simplified,
                                    "strict_command_sequence_equal": exact_cmd == ref_cmd,
                                    "strict_coordinate_count": len(exact_nums),
                                    "strict_max_coordinate_error": strict_error,
                                    "reference_fill": path.attrib.get("fill"),
                                    "reference_parent_id_hint": path.attrib.get("reference_parent_id"),
                                    "reference_subpath_index": path.attrib.get("subpath_index"),
                                    "decoded_fill": old.get("fill"), "class_name": obj["class_name"],
                                    "previous_score": old.get("score"), "previous_eligible": old.get("eligible"),
                                    "container_ids": old.get("container_ids", []),
                                    "previous_selected": obj["object_id"] in previous.get("selected_ids", []),
                                    "previous_suggested": obj["object_id"] in previous.get("suggested_ids", [])})
            if matches:
                result["positives"].extend(matches)
            else:
                result["unmatched"].append({"reference_path_id_hint": path.attrib.get("id"),
                                             "fill": path.attrib.get("fill"), "coordinate_count": len(expected)})
        positives = result["positives"]
        result["positive_ids"] = [p["object_id"] for p in positives]
        result["target_view_bbox"] = [min(p["bbox"][0] for p in positives), min(p["bbox"][1] for p in positives),
                                       max(p["bbox"][2] for p in positives), max(p["bbox"][3] for p in positives)] if positives else None
        result["reference_viewBox"] = xml.attrib.get("viewBox")
        paints = dict(Counter(p["reference_fill"] for p in positives))
        result["composite_requirements"] = {"required_fills": paints, "multi_color": len(paints) > 1,
                                            "white_elements_required": any(c.lower() in ("#fff", "#ffffff", "white") for c in paints),
                                            "required_object_count": len(positives), "preserve_reference_object_order": True}
        result["missed_by_previous_selection"] = [p["object_id"] for p in positives if not p["previous_selected"]]
        result["missed_by_previous_suggestions"] = [p["object_id"] for p in positives if not p["previous_suggested"]]
        result["previous_extra_ids"] = sorted(set(previous.get("selected_ids", [])) - set(result["positive_ids"]))
        result["metadata_ids_match_independent_matches"] = set(metadata.get("ids", [])) == set(result["positive_ids"])
        reports.append(result)
        (HERE / (ref.stem + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
        # Neutral gray exposes required white pieces; output is a QA composite only.
        panel = Image.new("RGB", (1100, 240), "#b8b8b8")
        draw = ImageDraw.Draw(panel)
        draw.text((10, 5), f"{len(reports)}. Reference (left) / previous extraction (right)", fill="black")
        candidates = [ref.with_suffix(".png"), previous_paths[0].parent / "result.png" if previous_paths else None]
        for side, image_path in enumerate(candidates):
            if image_path and image_path.exists():
                im = Image.open(image_path).convert("RGBA")
                im.thumbnail((530, 200))
                panel.paste(im, (10 + side * 550, 30 + (200 - im.height) // 2), im)
        panels.append(panel)
    (HERE / "audit_summary.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf8")
    sheet = Image.new("RGB", (1100, 240 * len(panels)), "white")
    for i, panel in enumerate(panels):
        sheet.paste(panel, (0, i * 240))
    sheet.save(HERE / "reference_vs_previous.png")
    print(json.dumps([{ "file": Path(r["source"]).name, "positive_ids": r["positive_ids"],
                       "unmatched": r["unmatched"], "fills": r["composite_requirements"]["required_fills"],
                       "missed": r["missed_by_previous_selection"]} for r in reports], ensure_ascii=False))


if __name__ == "__main__":
    main()
