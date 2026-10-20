# FlexiSIGN 8.1 native display findings

Target: `D:/Program Files (x86)/FlexiSIGN-PRO 8.1v1/Program/fscore.dll`, SHA-256 `bcddb4912f9b044f1ba6b0350d7145f85983ba0a376b9e4714558bf12197471d`. Static Ghidra 12.1.4 and PE exports only; no official binaries modified or executed. JSON files retain exact procedure identity, REA evidence ID, pseudocode and limitations.

## Confirmed black-overlay root cause

`ASolidBrush::flexiSerialize` at `0x10016170` writes field 1 as the integer member at `this+0x10`; loading defaults it to zero. Export `ASolidBrush::isShowWireFrameOnly` at `0x10002650` reads exactly that member. Thus field 1 is **wireframe-only**, not a solid black fill or overprint. Evidence `ev_fad3f5de987f5229874af8b610523c57cf37fefaf68837df44d141179cec70e7` and `ev_c92535e2ebe6c9063c97ec87000c9068aa9d8bc438173c37c4f56dd1175a62c3`.

`AObject::getDrawPenAndBrush` normal display mode 1 checks this flag, clears the supplied APen, uses the brush output color as a pen-color override, and clears the brush fill. Evidence `ev_74936904842d228fafdfc5f4fef8c7766bc031cfa5c274de2ad7c664dcd46bf9`, address `0x1002fee0`. `ADevice::initPenBrushColor` uses zero logical width for that color-only pen and a GDI cosmetic pen (minimum one display pixel), not the stored APen width. Evidence `ev_c26c722c7c43f3b4795104f51f624d38a8af4284be90cdcd18d5acbb32f9a012` at `0x100994b0`.

Implementation: `fill=none`, stroke=brush display/output RGB, cosmetic hairline width. SVG non-scaling stroke is a renderer-dependent implementation choice. Printing mode 4 suppresses wireframe-only objects entirely; normal preview must retain outlines.

## Flags, pen and hierarchy serialization

- AFigure fields: 0=AObject; 1=6-double matrix; 2=APen object; 3=ABrush object. Evidence `ev_4c915d80f8e785083731335c1f7c84dd6f253224d6a97b7ae2438209c6b8dab1` at `0x10025b70`.
- AObject field 1=u32 flags (`this+8`), field 2=name, field 3=another u32. Evidence `ev_01501ab568bc19191bf63136bfdde14d3eb343ac58f470fd3b225077f2a2b92a` at `0x1002ed10`.
- AParent field 0=AFigure; field 1=child AObList. AGroup field 0=AParent. AEffect field 0=AGroup. AComposite field 0=AEffect. Each of these last three wrappers has no own extra fields in this build. See `parentser.json`, `groupser.json`, `effectser.json`, `compositeser.json`.
- APen fields: 10=ARoot; 11=APenItem reference; 12=6-double matrix; 13=integer flags, stored as a byte at `this+0x38`. It does NOT share the ABrush reference/owner schema. Evidence `ev_e37abffc1e54dd176a081932b94fcacfc8a0832d77fa72b7c44796a21f3875a2` at `0x10014ed0`.

`AGroup::draw` first checks object flag `0x08000000` and skips the entire group when set. Otherwise it iterates `GetHeadPosition` then `GetNext`, drawing children in serialized list order. Evidence `ev_94a1bbce6daa12d3d39f9489d1745a9af80579e4b3cf48b5087f072f665d75ec` at `0x10027c80`. This confirms group visibility behavior, not every leaf class visibility implementation.

`AComposite::draw` normal mode records descendant polygons with draw flag `0x40000`, then draws the poly-polygon with the composite's own pen/brush. Its solid fill branch explicitly selects `SetPolyFillMode(...,1)` (GDI ALTERNATE/evenodd) and a null pen before PolyPolygon. Outline/recording modes delegate to AGroup. Evidence `ev_4576472d080cf9df340a25900214b53a8a586487efcd5446529c44f6dffa29f0`. Therefore independently filling every descendant is not equivalent to compound rendering. Preserve the compound as a single path with evenodd and parent appearance for normal preview. Special effects/clipping branches remain more complex than this model.

## Spot and generic colors

ASpotColorItem field 1 is the tint percentage byte. Native getColor applies per channel `clamp(truncTowardZero((base-255)*tint/100)+255,0,255)`. Exact evidence `ev_42c5046b8dc2cf3f7c2500826900bad978fe48782906d8c43222f37fa89cc1cb`; serializer `ev_06b288eecf374e7ce3b0efdfca4658df482b686ca74756870e9dc3b4d380fb8f`.

`fsutil.dll` AColor::flexiSerialize at `0x10003710` stores field 0 as three little-endian ushort values, each channel shifted left 8, R then G then B. Reading takes each ushort's high byte. Thus the six-byte payload's indices 1,3,5 are the RGB channels. See `generic_color_capstone.json` with artifact SHA and static instructions. Do not use division by 257.

Color-management output is a separate path: stored RGB does not prove equality to an ICC-managed screen rendering. Missing embedded images, text/font shaping, gradients beyond observed enums, effects, clipping, layers, and unsupported geometry classes can still prevent an exact official match. No static investigation here proves a complete renderer.

## Viewport performance investigation (2026-10-20)

The same installed `fscore.dll` identity was reopened with REA/Ghidra. `ADocView::OnDraw` delegates to region redraw/copy operations. `ADeviceMgr::redrawRegion` sets clipping and can render into an offscreen device; `copyRegion` reuses that buffer when valid. `resizeBuffers` includes overlapping-region reuse and coordinate synchronization; two terminal-flow warnings mean its whole control flow remains incompletely recovered. `AObject::getFinalResult` stores a result pointer on the object and conditionally builds it when absent. These are static observations, not an official FPS benchmark or proof of GPU use. Exact pseudocode and limitations are retained in [performance.json](performance.json); the evidence ledger and local implementation measurements are in [vector preview performance](../../docs/vector-preview-performance.md).

## Metadata change verification

Added only `object_flags` and raw `pen_hex` to core/fs_geometry.py. Parsed all ten case files successfully, retaining geometry counts (3309, 4985, 4541, 10190, 33397, 54210, 25702, 20889, 24988, 6205). No parsed leaf in those ten files has bit 0x08000000. No additional geometry decoder implemented in this subtask.
