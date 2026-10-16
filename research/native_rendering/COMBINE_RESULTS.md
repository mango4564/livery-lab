# Combine effects and persisted results

Additional artifact: `D:/Program Files (x86)/FlexiSIGN-PRO 8.1v1/Plugins/fseffects.dll`, SHA-256 `faa7dff3adfa33deef49e87dfd54f65dc5bb8533894c8edda4101e516102c06e`. Static evidence only.

RTTI/vtable binding shows ACombineCutOut, ACombineWeld, ACombineCommon draw slot 0x100 points to thunk `0x100496b4`, imported `AEffect::draw`. Native fscore AEffect::draw `0x10025200` delegates normal rendering to AObject::draw `0x10032dc0`; that routine calls getFinalResult(this,1) then AResult::draw. It does NOT normally draw original operands as independent visible artwork. Exact static call instructions saved `effect_result_draw_capstone.json`.

Use the persisted result path list when the source document carries it; do not recompute approximate booleans and do not expose operand paths alongside the result. Source sample's additional AEffect version2 field1 appears consistent with a persisted AResult body (whose field0 is AObList, field1 optional owner). This last file-schema association is source-format inference: installed 8.1's AEffect serializer only serializes a field0 wrapper around AGroup, and cannot prove newer version2 field1. Validate the sample's counted AObList wrapper and result boundaries independently.

## Generation evidence

- Weld getNewResult `0x10001630`: TOF_CombineWeld on all child operands, result receives parent pen/brush and inherited object attributes; on failure uses group result. `ev_98dffe1d396689a3ea9f6c62b18cd53b09c672c11fd554542a9002a354ec940a`, `combineweld.json`.
- CutOut getNewResult `0x10001800`: takes **tail child as cutter**; loops preceding children in head-to-tail order, loops each child's final-result paths, adds each before cutter in TOF_InputTwoGroup, calls TOF_CombineCutOut, outputs with that preceding path's attributes. `ev_9f2847c29ca0b373af61e54a6b824687c385d260d42233ff3efa37263fa6fcba`, `combinecutout.json`. Subtraction orientation inferred from named operation and input ordering; TOF implementation itself not decompiled here.
- Common getNewResult `0x10001aa0`: loops each tail-child result path; pairwise TOF_CombineCommon with every preceding child result path, unions those pairwise results with TOF_CombineWeld, outputs with tail path attributes. It is **tail intersect union(previous operands)**, not necessarily intersection of ALL operands. `ev_7a44283174b19539ff64de3c7065cf27e1f766c7e14016b176c88f2aab102ed9`, `combinecommon.json`.
- All three serialize only a field0 AEffect wrapper in this installed version (`0x10001170`). `ev_2f346580289b38a8409b30e2a7ec5d271fd5a480598a5f2a814c59647e2ba8fe`, `combineser.json`. No class-specific keep-copy serialization flag found in this focused method, which does not prove such behavior absent elsewhere.

Native AObject::draw checks hidden bit 0x08000000 before drawing its final result, confirming the earlier group hidden behavior applies here too.
