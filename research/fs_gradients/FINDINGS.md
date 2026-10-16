# Original FS gradients and spot display colors

## Observations from source records

Source: ten original FS files in `../全车矢量图素材/10个FS案例`, independent of exported reference artwork.

* `AGradientBrushItem Data` uses the same 32-bit counted reference framing as other reference streams. In sample 3261042, all 12 reference records decode without errors.
* Its field1 is a TLV gradient definition. Field5 is mode (observed 1/2); field7 contains flags (observed 256/512). Field9.field5.field2 is a `u16` count and nested `(id:u32,class:u32,size:u32,payload,0d90)` stop records.
* Each nested `AGradientColor` stores field2 = double position in [0,1]; field1 = cached display RGB through the generic AColor serializer. Field3 retains a full original color-item definition, which can be CMYK even when the display RGB is cached explicitly in field1. Using only nested RGBColorItem classes loses CMYK stops.
* Sample gradient80 stops: 0=#ffffff, .6375142793797337=#979799, 1=#6b6c72. Gradient154 contains five black/red stops, including middle #c60000/#ff0000/#d00000.
* Per-object `AGradientBrush.field0` contains field0 = brush-reference base and field1 = six-double normalized-to-page matrix. Brush-reference base field1 is gradient reference ID, field2 owner ID. Appearance field1 and field5 are start/end points; field6 is observed `1`.
* These per-object points must override shared reference coordinates. Sample reference176 has (208.646,-34.345) to (208.646,-105.814); appearance307 instead has normalized (.742373,1.046283) to (.742373,.056192). Its matrix maps directly to geometry bbox (624.990,403.668)-(632.965,407.048).
* The coordinate matrix can differ from the path bbox after edits. Preserve the serialized frame rather than recomputing the control-point envelope. Some gradients deliberately extend past normalized [0,1].

## Native corroboration

The native-colors investigation found `fsutil.dll AColor::flexiSerialize` at 0x10003710: serialized RGB are three little-endian unsigned shorts with the channel shifted left by eight. Decode each high byte (not division by257). Native `AColor::Red` 0x10001bf0 accesses the red channel at object+4. See that investigation's report for disassembly and exact artifact identity.

`ASpotColorItem::getColor` 0x10016a30 applies, per channel, `clamp(trunc((base - 255) * tint / 100) + 255, 0, 255)`. Evidence: `ev_42c5046b8dc2cf3f7c2500826900bad978fe48782906d8c43222f37fa89cc1cb`, saved in `research/native_rendering/spotcolor.json`. Tint serialization from field1 to object+0x40 is corroborated by 0x10016d10, Evidence `ev_06b288eecf374e7ce3b0efdfca4658df482b686ca74756870e9dc3b4d380fb8f`.

## Rendering interpretation and limits

Mode1 is interpreted as SVG linear; mode2 as radial. This is an inference from the stored coordinates and common FS gradient layouts (radial wheels have center .5,.5 and endpoint1,.5), not yet a native gradient renderer trace. SVG retains stop ordering and original positions, uses `gradientUnits=userSpaceOnUse`, the exact serialized appearance matrix as `gradientTransform`, and sRGB interpolation with pad. Radial local circles become ellipses through nonuniform matrices. Full native ICC/color interpolation, repeat flags, nonstandard gradient modes, clipping, blends, glyphs and hidden layers remain outside this module's claimed equivalence.

## Verification and API

`python -m unittest test_fs_gradients -v` exercises cached RGB, native spot tint rounding, nested stop offsets, mirror-ready per-object coordinate frames, radial ellipse mapping and malformed input. All 5 tests pass.

All ten source files' reference gradient lists decode (11 or12 refs each). Validated discovered path gradient appearances per source: 3261042=105,3261588=146,3261754=109,3261074=250,6827diesel=96,7260798=132,7260812=102,3261129=109,3261627=138,3261895=202. This covers path appearances, not every container or glyph gradient.

* `decode_gradients(ole)` returns ID-keyed gradient tables, retaining explicit errors for unsupported records.
* `gradient_appearances(raw, classes, word)` returns gradient refs, per-object matrix/start/end.
* `gradient_svg(gradient, appearance, identifier)` returns an SVG paint-server definition or None for unsupported mode.
* `display_color(raw)` decodes generic cached RGB; `spot_display_color(record)` uses the native tint formula.

Parser edge case: object ID36876 (`0x900d`) is valid and begins with the same bytes as an end sentinel. Only consume sentinels after a parsed payload (or alone at end), never blindly at the start of a record.
