"""Reference is used only by this acceptance check, never by the decoder."""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from fs_geometry import extract_geometry

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
reports = []
for source in sorted((ROOT / "全车矢量图素材").glob("*.fs")):
    try:
        result = extract_geometry(source)
        reports.append({"file": source.name, **result["geometry_stats"], "warnings": result["warnings"]})
    except Exception as exc:
        reports.append({"file": source.name, "error": str(exc)})
(HERE / "geometry_corpus_report.json").write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf8")

sample = ROOT / "全车矢量图素材/乾途单挡3.6米车高（车长11.7）3261754.fs"
reference = Path("C:/Users/admin/AppData/Local/Temp/彩条.svg")
decoded = extract_geometry(sample)["objects"]
targets = [e for e in ET.parse(reference).getroot().iter() if e.tag.endswith("path")]
numbers = lambda d: [float(x) for x in re.findall(r"[-+]?(?:\d*\.)?\d+(?:[eE][-+]?\d+)?", d)]
matches = []
for target in targets:
    expected = numbers(target.attrib["d"])
    best = None
    for obj in decoded:
        actual = numbers(obj["d"])
        if len(actual) != len(expected):
            continue
        error = max(abs(a - b) for a, b in zip(actual, expected))
        if best is None or error < best["max_coordinate_error"]:
            best = {"reference_id": target.attrib.get("id"), "decoded_object_id": obj["object_id"],
                    "source_offset": obj["source_offset"], "coordinate_count": len(actual),
                    "max_coordinate_error": error,
                    "command_sequence_equal": re.findall("[MLCZ]", obj["d"]) == re.findall("[MLCZ]", target.attrib["d"])}
    matches.append(best)
(HERE / "reference_acceptance.json").write_text(json.dumps(matches, indent=2), encoding="utf8")
print(json.dumps({"corpus": reports, "reference_matches": matches}, ensure_ascii=False))
