# 候选复核导出多层分类目录实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将用户完成六类复核后的导出成果由平铺目录结构改为按类别分层存放于 `{车名}/` 下的子目录中（`整车底图/`、`车身/`、`车漆/`、`彩条/`、`文字/`、`logo/`、`门徽/`），并保持清单记录与下游读取协议一致。

**Architecture:** 修改 `reviewed_exports.py` 中的成品导出路径生成逻辑，为 `vehicle-base.*` 建立 `整车底图/` 目录，为各类已确认图案建立对应类别子目录（未选或无图案的类别不建立空目录），`manifest['categories'][...]['files']` 与 `manifest['artifact_sha256']` 使用子目录相对 POSIX 路径，保持原子事务替换与历史归档；随后更新全部相关的单元与回归测试。

**Tech Stack:** Python 3.13, ElementTree, PIL, unittest

## Global Constraints
- `BASE_DIR = '整车底图'`
- `CATEGORY_DIRS = {'车身': '车身', '车漆': '车漆', '彩条': '彩条', '文字': '文字', 'logo': 'logo', '门徽': '门徽'}`
- `复核记录.json` 保持在车名根目录下
- `manifest['artifact_sha256']` 的 key 为基于车名根目录的相对 POSIX 路径（如 `'整车底图/vehicle-base.svg'`、`'车身/xxx.svg'`）
- 保持 `export_reviewed` 原子替换与 `.review_history` 归档事务性

---

### Task 1: 修改 `reviewed_exports.py` 实现多层目录分类导出

**Files:**
- Modify: `reviewed_exports.py:13-240`
- Test: `test_reviewed_exports.py`

**Interfaces:**
- Produces: `export_reviewed(result, choices, output_root, *, patterns=None) -> {'output_dir': str, 'manifest': dict, 'previous_version': str | None}`

- [ ] **Step 1: 在 `reviewed_exports.py` 中添加目录映射常量**
在 `LABELS` 旁边定义：
```python
BASE_DIR = '整车底图'
CATEGORY_DIRS = {'车身': '车身', '彩条': '彩条', '文字': '文字', 'logo': 'logo', '门徽': '门徽', '车漆': '车漆'}
```

- [ ] **Step 2: 修改底图写入逻辑**
当 `selected['车漆']` 时：
```python
        if selected['车漆']:
            from geometry import union_bounds
            bounds = union_bounds([by_id[i] for ids in selected.values() for i in ids])
            categories = {n: ids if n in ('车身', '车漆') else [] for n, ids in selected.items()}
            svg, info = layered_svg(result['objects'], categories, bounds=bounds, width=2400,
                metadata={'source_sha256': digest, 'human_reviewed': True},
                layer_names=('车漆', '车身'), source_order_fallback=True)
            base_dir = staging / BASE_DIR
            base_dir.mkdir(parents=True, exist_ok=True)
            (base_dir/'vehicle-base.svg').write_text(svg, encoding='utf8')
            rasterize(svg, base_dir/'vehicle-base.png', max_width=2400)
            manifest['layer_status'] = info['layer_status']
            manifest['base_rendering'] = info['rendering']
            manifest.setdefault('schema_version', 2)
```

- [ ] **Step 3: 修改各类别图案写入逻辑**
为非空类别创建子目录，并将 SVG/PNG 写入对应子目录，同时更新 `files` 记录为带子目录前缀的相对路径：
```python
        for name, label in LABELS.items():
            ids = selected[name]
            files = []
            entries = reviewed_patterns[name] if patterns is not None else ([{'selected_ids': ids}] if ids else [])
            pattern_records = []
            folder_name = CATEGORY_DIRS[name]
            target_dir = staging / folder_name
            if entries:
                target_dir.mkdir(parents=True, exist_ok=True)
            for index, entry in enumerate(entries, 1):
                pattern_ids = entry['selected_ids']
                id_set = set(pattern_ids)
                objects = [o for o in result['objects'] if o['id'] in id_set]
                svg, _ = layered_svg(objects, {name: pattern_ids}, width=2400, layer_names=(name,),
                    metadata={'source_sha256': digest, 'category': label, 'selected_ids': pattern_ids,
                              'source_view': entry.get('source_view', '待确认视图'),
                              'source_region': entry.get('source_region'),
                              'region_name': entry.get('region_name'),
                              'human_reviewed': True})
                stem = (f"{entry.get('region_name', entry.get('source_view'))}_{label}_{index:02d}_{source.stem}"
                        if patterns is not None else label + '_' + source.stem)
                (target_dir/(stem+'.svg')).write_text(svg, encoding='utf8')
                rasterize(svg, target_dir/(stem+'.png'), max_width=2400)
                pair = [f"{folder_name}/{stem}.svg", f"{folder_name}/{stem}.png"]
                files.extend(pair)
                pattern_records.append(dict(entry, files=pair))
            manifest['categories'][label] = {'status':'reviewed' if ids else 'confirmed_absent',
                                              'selected_ids':ids, 'files':files}
            if patterns is not None:
                manifest['categories'][label]['patterns'] = pattern_records
            # Keep exact exported pattern memberships/files for placement hints.
            reviewed_patterns[name] = pattern_records
```

- [ ] **Step 4: 修改槽位挂载与 placements 路径**
更新底图文件路径与 placements 文件位置：
```python
        if selected['车漆']:
            from core.placements import attach_placements
            placement_patterns = reviewed_patterns
            if patterns is None:
                placement_patterns = {}
                for name in LABELS:
                    entries = []
                    for group in split_by_region(selected[name], assignments,
                            objects=result['objects'] if name in ('车漆', '彩条') else None):
                        entry = {'selected_ids': group, 'files': manifest['categories'][LABELS[name]]['files']}
                        region = region_for_ids(group, assignments, regions)
                        if region:
                            entry.update(source_region=region['id'], region_name=region['name'],
                                         source_view=region['source_view'])
                        entries.append(entry)
                    placement_patterns[name] = entries
            base_dir = staging / BASE_DIR
            base_path = base_dir / 'vehicle-base.svg'
            svg, placements = attach_placements(base_path.read_text(encoding='utf8'),
                base_dir / 'vehicle-base.png', result['objects'], placement_patterns, regions,
                digest, individual_patterns=patterns is not None)
            base_path.write_text(svg, encoding='utf8')
            (base_dir / 'vehicle-base.placements.json').write_text(
                json.dumps(placements, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')
            manifest['placements'] = {'schema_version': placements['schema_version'],
                                      'template_id': placements['template_id'],
                                      'file': f'{BASE_DIR}/vehicle-base.placements.json',
                                      'slot_count': len(placements['slots'])}
```

- [ ] **Step 5: 修改 `artifact_sha256` 递归扫描**
```python
        manifest['artifact_sha256'] = {
            p.relative_to(staging).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in staging.rglob('*') if p.is_file() and p.name != '复核记录.json'
        }
        (staging/'复核记录.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf8')
```

---

### Task 2: 更新 `test_reviewed_exports.py` 基础导出测试

**Files:**
- Modify: `test_reviewed_exports.py:31-75`

- [ ] **Step 1: 更新 `test_exact_source_folder_and_five_named_transparent_pairs`**
断言顶级目录包含子目录与 `复核记录.json`，且各分类子目录包含对应的 SVG/PNG：
```python
    def test_exact_source_folder_and_five_named_transparent_pairs(self):
        published=export_reviewed(self.result,self.choices,self.output)
        folder=Path(published['output_dir'])
        stem=Path(self.result['source']).stem
        self.assertEqual(folder,self.output/stem)
        from reviewed_exports import CATEGORY_DIRS
        expected_dirs = {CATEGORY_DIRS[name] for name in LABELS if self.choices[name]}
        self.assertEqual({p.name for p in folder.iterdir()}, expected_dirs | {'复核记录.json'})
        for name, label in LABELS.items():
            if not self.choices[name]:
                continue
            dir_path = folder / CATEGORY_DIRS[name]
            self.assertTrue(dir_path.is_dir())
            for suffix in ('.svg', '.png'):
                file_path = dir_path / (label + '_' + stem + suffix)
                self.assertTrue(file_path.is_file())
                rel_path = f"{CATEGORY_DIRS[name]}/{label}_{stem}{suffix}"
                self.assertIn(rel_path, published['manifest']['artifact_sha256'])
        for p in folder.rglob('*.png'):
            with Image.open(p) as im:
                self.assertEqual(im.mode,'RGBA')
                self.assertEqual(im.getextrema()[3][0],0)
                self.assertEqual(im.getextrema()[3][1],255)
```

- [ ] **Step 2: 更新 `test_explicit_absence_has_no_empty_files`**
断言空类别不生成对应子目录：
```python
    def test_explicit_absence_has_no_empty_files(self):
        self.choices['logo']=None
        p=export_reviewed(self.result,self.choices,self.output)
        self.assertFalse((Path(p['output_dir'])/'logo').exists())
        self.assertFalse(list(Path(p['output_dir']).rglob('logo*')))
        self.assertEqual(p['manifest']['categories']['logo']['status'],'confirmed_absent')
```

- [ ] **Step 3: 更新 `test_reexport_archives_old_and_removes_now_absent_pair`**
断言旧版本子目录归档到 previous_version，而新版本不再包含 logo 子目录：
```python
    def test_reexport_archives_old_and_removes_now_absent_pair(self):
        first=export_reviewed(self.result,self.choices,self.output)
        self.choices['logo']=None
        self.choices['彩条']=['5']
        second=export_reviewed(self.result,self.choices,self.output)
        self.assertEqual(first['output_dir'],second['output_dir'])
        self.assertTrue((Path(second['previous_version'])/'logo').is_dir())
        self.assertTrue(list(Path(second['previous_version']).rglob('logo*.svg')))
        self.assertFalse((Path(second['output_dir'])/'logo').exists())
        self.assertEqual(second['manifest']['categories']['彩条']['selected_ids'],['5'])
```

- [ ] **Step 4: 运行 `python -X utf8 -m unittest test_reviewed_exports.py -v`**
验证 Task 1 & 2 修改全部通过。

- [ ] **Step 5: 提交任务更改**
```bash
git add reviewed_exports.py test_reviewed_exports.py
git commit -m "feat: export reviewed components into category subdirectories"
```

---

### Task 3: 适配全量关联测试集

**Files:**
- Modify: `tests/test_paint_layers.py`
- Modify: `tests/test_vehicle_placements.py`
- Modify: `tests/test_directional_exports.py`
- Modify: `tests/test_source_regions.py`
- Modify: `tests/test_review_body_separation.py`
- Modify: `tests/test_text_logo_separation.py`
- Modify: `tests/test_emblem_logo.py`

- [ ] **Step 1: 适配 `tests/test_paint_layers.py`**
- `folder/'vehicle-base.svg'` -> `folder/'整车底图'/'vehicle-base.svg'`
- `self.assertIn('整车底图/vehicle-base.svg', published['manifest']['artifact_sha256'])`

- [ ] **Step 2: 适配 `tests/test_vehicle_placements.py`**
- `folder/'vehicle-base.placements.json'` -> `folder/'整车底图'/'vehicle-base.placements.json'`
- `folder/'vehicle-base.svg'` -> `folder/'整车底图'/'vehicle-base.svg'`
- `folder/'vehicle-base.png'` -> `folder/'整车底图'/'vehicle-base.png'`
- `self.assertIn('整车底图/vehicle-base.placements.json', published['manifest']['artifact_sha256'])`
- 验证 `data['slots']` 中的 `f in s['files']` 检查 `(folder / f).exists()` 自动通过

- [ ] **Step 3: 适配 `tests/test_directional_exports.py`**
- 将文件名断言 `filename.startswith(...)` 适配为 `Path(filename).name.startswith(...)` 或 `filename.startswith('彩条/' + ...)`

- [ ] **Step 4: 适配 `tests/test_source_regions.py` 及其他测试**
- 检查涉及 `entries[0]['files'][0].startswith(...)` 的断言，适配子目录前缀

- [ ] **Step 5: 运行全量回归测试**
运行 `python -X utf8 -m unittest discover -s . -p "test_*.py" -v`
确保 319+ 项测试 100% 全部通过。

- [ ] **Step 6: 提交测试适配改动**
```bash
git add tests/
git commit -m "test: update regression tests for categorized export subdirectories"
```

---

### Task 4: 更新交接与开发文档

**Files:**
- Modify: `README.md`
- Modify: `ui_README.md`
- Modify: `docs/vehicle-paint-recoloring.md`
- Modify: `docs/vehicle-placement-integration.md`
- Modify: `docs/stripe-view-exports.md`

- [ ] **Step 1: 更新各文档中的导出目录层级描述**
将 `vehicle-base.*` 标注为保存在 `整车底图/` 目录，各图案标注为保存在 `车身/`、`车漆/`、`彩条/`、`文字/`、`logo/`、`门徽/` 目录中。

- [ ] **Step 2: 提交文档更新**
```bash
git add README.md ui_README.md docs/
git commit -m "docs: document categorized export subdirectories"
```
