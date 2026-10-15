# FS attribute findings (independent read-only research)

Source: original corpus only; no target SVG coordinates or attachment metadata are used by this module.

## Confirmed structure

- `ClassTable` class IDs vary per document. The name section is decoded dynamically.
- Reference streams use 32-bit TLV lengths even for the sample's 64-bit object stream.
- Reference field 2 is `count:u16` then repeated `id:u32,class:u32,length:u32,payload,0d90` records.
- `ASolidBrushItem.field1` is a color reference. `ARGBColorItem.field11` is stored display RGB (three bytes). RGB color names are held in the common item base.
- Object `ASolidBrush.field0.field1` references a shared solid brush; `.field2` identifies the owner object. Appearance class is resolved from the document-local class table.
- Sample targets 233/235/237 all reference brush 41 -> color 5 -> stored RGB #167ce2. This is from source FS, independent of attachment output.
- The sample has 36 color references, 33 solid brushes, three pen references and five layers.

## Empty / unknown behavior

- A four-zero-byte appearance means an empty serialized appearance list. It is NOT proven transparent fill: inheritance and compound semantics are unresolved.
- Unsupported appearance (gradient, pen, etc.), spot colors and CMYK preserve an explicit unknown RGB status. No fallback black is claimed to be original color.
- The module returns empty list for an empty appearance, and fill_status `empty_appearance_inheritance_unknown`; supported RGB fill status is `stored_display_rgb`.
- Stored RGB does not establish ICC-correct print output or physical dimensions.

## Coordinates and hierarchy

- Sample scan validates 899 container records: 691 AGroup, 188 AComposite, 13 ACombineWeld, five ACombineCutOut, one ACombineCommon, one ALayer.
- All 898 non-layer container matrices are nonidentity. Their relationship to emitted page-coordinate geometry is unproven.
- Sample target 233 is directly under identity ALayer 195 and outside all group/compound spans; same target family should be checked with `group_ancestors`.
- Parent agent's rendering established that raw source geometry aligns with the embedded sample preview; applying observed own matrices corrupts layout. Therefore these matrices must remain unapplied metadata pending their serializer semantics being established. Target AEditPath 233 has only top fields 3 and 1; there is no alternate top-level field 0 drawing base.
- Byte-span containment gives serialized ancestry, not boolean-operation order, visibility or clipping guarantees. Scan records expose `requires_boolean_semantics` and visibility `unknown`.

## API

`decode_references(ole)` -> classes/colors/solid_brushes/pens/layers dictionaries.

`resolve_appearance(raw, refs, word=8)` -> observed appearance records with original brush/color references.

`load_appearances(ole, objects, word=8)` -> annotated object list and refs, leaving input records unmodified.

`scan_container_ranges(inflated_objects, refs['classes'], word)` -> validated container spans, own matrices and diagnostic semantic flags.

`group_ancestors(record, containers)` -> containing IDs from outside to inside.

Current limitations: full gradient semantics, pen width units, fill inheritance, hidden flags, boolean/clip evaluation and physical scale remain unresolved.
