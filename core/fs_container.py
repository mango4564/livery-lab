"""Independent, read-only FS container inspection; no original files are modified.

Preview decoder is validated on the sample corpus. Object stream decompression
is deliberately separate from geometry interpretation: this does not claim to
decode paths, transforms, clipping, or fill references.
"""
import argparse
import hashlib
import json
import struct
import zlib
from pathlib import Path

import olefile
from PIL import Image


def fields(data, start=1, word=4):
    """Version byte followed by id:u8,length:u32/u64,payload TLVs."""
    result = {}
    pos = start
    header = 1 + word
    while pos < len(data):
        if pos + header > len(data):
            raise ValueError(f"truncated TLV header at {pos}")
        size = int.from_bytes(data[pos + 1:pos + header], "little")
        if size < header or pos + size > len(data):
            raise ValueError(f"invalid TLV size {size} at {pos}")
        if data[pos] in result:
            raise ValueError(f'duplicate TLV field at {pos}')
        result[data[pos]] = data[pos + header:pos + size]
        pos += size
    return result


def decode_preview(data):
    f = fields(data)
    height = int.from_bytes(f[4], "little")
    width = int.from_bytes(f[5], "little")
    if width<=0 or height<=0 or width*height>16_000_000:
        raise ValueError('unsupported preview dimensions')
    depth = f[6][0]
    channels = f[7][0]
    if depth != 8 or channels != 3:
        raise ValueError(f"unsupported preview depth/channels {depth}/{channels}")
    count = int.from_bytes(f[12][:4], "little")
    palette = f[12][4:]
    if count > 256 or len(palette) != count * 3:
        raise ValueError("invalid palette")
    # Rows may have DIB-style four-byte alignment. Current corpus widths are 256.
    raw = f[13]
    stride = len(raw) // height
    if len(raw) % height or stride < width:
        raise ValueError("invalid pixel count")
    raw = b"".join(raw[y * stride:y * stride + width] for y in range(height))
    image = Image.frombytes("P", (width, height), raw)
    image.putpalette(palette.ljust(768, b"\0"))
    return image.convert("RGB"), {"width": width, "height": height, "depth": depth,
                                 "palette_entries": count, "source_stride": stride}


def unpack_objects(data):
    if len(data)<6:
        raise ValueError('truncated object header')
    if data[:2] != b"\x03\x02":
        # Earlier files store the TLV object tree directly with 32-bit lengths.
        if data[:2] == b"\x02\x00" and 5 <= struct.unpack_from("<I", data, 2)[0] <= len(data):
            return data, {"compression": "none", "tlv_word_bytes": 4,
                          "expected_inflated": len(data)}
        raise ValueError("unsupported object compression header")
    compressed_region = struct.unpack_from("<I", data, 2)[0]
    if len(data)<18:
        raise ValueError('truncated compression header')
    old_total = struct.unpack_from("<I", data, 6)[0]
    if old_total:
        reserved, expected = None, old_total
        block_size = struct.unpack_from("<I", data, 10)[0]
        pos, word = 14, 4
    else:
        reserved, expected, block_size = struct.unpack_from("<III", data, 6)
        pos, word = 18, 8
    if not 0<expected<=512*1024*1024 or not 0<block_size<=64*1024*1024:
        raise ValueError('object stream exceeds decoder limits')
    chunks = [];total=0
    blocks = []
    while total < expected:
        if pos+4>len(data):
            raise ValueError('truncated compressed block header')
        size = struct.unpack_from("<I", data, pos)[0]
        payload = data[pos + 4:pos + 4 + size]
        if len(payload) != size:
            raise ValueError("truncated compressed block")
        decoder=zlib.decompressobj()
        decoded = decoder.decompress(payload,block_size+1)
        if len(decoded) > block_size or not decoder.eof or decoder.unused_data:
            raise ValueError("block exceeds advertised size")
        if not decoded:
            raise ValueError('empty compressed block')
        chunks.append(decoded)
        total+=len(decoded)
        blocks.append({"offset": pos, "compressed": size, "inflated": len(decoded)})
        pos += 4 + size
    # Flexi pads the final compressed block to the advertised block size. The
    # declared total is the useful object-tree length, not the padded byte count.
    if total-expected>=block_size:
        raise ValueError('inflated stream exceeds final-block padding allowance')
    decoded = b"".join(chunks)[:expected]
    return decoded, {"compression": "zlib-blocks", "tlv_word_bytes": word,
                     "expected_inflated": expected, "block_size": block_size,
                     "compressed_region": compressed_region, "reserved": reserved,
                     "blocks": blocks, "tail_hex": data[pos:].hex()}


def inspect(path, destination, dump_objects=False):
    result = {"source": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    out = destination / path.stem
    out.mkdir(parents=True, exist_ok=True)
    with olefile.OleFileIO(path) as ole:
        result["streams"] = {"/".join(p): ole.get_size(p) for p in ole.listdir()}
        try:
            image, info = decode_preview(ole.openstream("FlexiSIGN Contents/Preview").read())
            image.save(out / "preview.png")
            result["preview"] = info
        except Exception as exc:
            result["preview_error"] = str(exc)
        try:
            raw, info = unpack_objects(ole.openstream("FlexiSIGN Contents/Page 1/Objects").read())
            result["objects"] = info
            if dump_objects:
                (out / "objects.bin").write_bytes(raw)
        except Exception as exc:
            result["objects_error"] = str(exc)
    (out / "inspection.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dump-objects", action="store_true")
    args = parser.parse_args()
    sources = sorted(args.source.glob("*.fs")) if args.source.is_dir() else [args.source]
    results = [inspect(p, args.out, args.dump_objects) for p in sources]
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "inventory.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"files": len(results), "previews": sum("preview" in r for r in results),
                      "objects_unpacked": sum("objects" in r for r in results)}, ensure_ascii=False))
