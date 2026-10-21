# 候选复核导出多层分类目录设计方案

## 1. 概述与目标

在彩条提取工作台（`livery-lab`）中，用户完成六类图案（车身、车漆、彩条、文字、logo、门徽）候选复核并点击“导出六类已确认成品”时，目前导出的所有文件（SVG、透明 PNG、整车底图及复核记录 JSON）均直接保存在以车名命名的根目录下，导致文件散落、结构混乱。

本设计的目标是：在车名目录下增加清晰的中间分类子目录，使导出成果结构化、规范化，便于用户检索与下游系统集成。

## 2. 导出目录结构设计

### 2.1 目录组织层级
当用户在复核窗口导出成品时，目标保存目录结构如下：

```text
成品保存根目录/
└── {车名}/
    ├── 整车底图/
    │   ├── vehicle-base.svg
    │   ├── vehicle-base.png
    │   └── vehicle-base.placements.json
    ├── 车身/
    │   ├── {视图}_{车身}_{序号}_{车名}.svg
    │   └── {视图}_{车身}_{序号}_{车名}.png
    ├── 车漆/
    │   ├── {视图}_{车漆}_{序号}_{车名}.svg
    │   └── {视图}_{车漆}_{序号}_{车名}.png
    ├── 彩条/
    │   ├── {视图}_{彩条}_{序号}_{车名}.svg
    │   └── {视图}_{彩条}_{序号}_{车名}.png
    ├── logo/
    │   ├── {视图}_{logo}_{序号}_{车名}.svg
    │   └── {视图}_{logo}_{序号}_{车名}.png
    ├── 门徽/
    │   ├── {视图}_{徽章}_{序号}_{车名}.svg
    │   └── {视图}_{徽章}_{序号}_{车名}.png
    ├── 文字/
    │   ├── {视图}_{文字}_{序号}_{车名}.svg
    │   └── {视图}_{文字}_{序号}_{车名}.png
    └── 复核记录.json
```

### 2.2 命名与映射规则
1. **类别与目录映射**：
   - `车身` -> `车身/`
   - `车漆` -> `车漆/`
   - `彩条` -> `彩条/`
   - `文字` -> `文字/`
   - `logo` -> `logo/`
   - `门徽` -> `门徽/`（界面标签与用户偏好，对应内部识别标签 `徽章`）
2. **整车底图目录**：
   - 包含由车身与车漆合成的 `vehicle-base.svg`、`vehicle-base.png` 以及槽位协议 `vehicle-base.placements.json`，统一保存在 `整车底图/` 目录。
3. **空类别处理**：
   - 若某类别经复核标记为“无图案（confirmed_absent）”或未选择任何对象，不创建对应的空目录。
   - 若未选择车漆，不创建 `整车底图/` 目录。
4. **根文件保持**：
   - `复核记录.json` 继续保存在车名根目录下（即 `{车名}/复核记录.json`），以便于后续再次打开复核窗口时通过 `_restore()` 自动定位并恢复。

## 3. 详细设计与代码变动

### 3.1 `reviewed_exports.py`
1. **配置常量**：
   ```python
   BASE_DIR = '整车底图'
   CATEGORY_DIRS = {
       '车身': '车身',
       '车漆': '车漆',
       '彩条': '彩条',
       '文字': '文字',
       'logo': 'logo',
       '门徽': '门徽'
   }
   ```
2. **底图生成逻辑**：
   - 当 `selected['车漆']` 存在时：
     - 创建 `staging / BASE_DIR` 目录。
     - 写入 `staging / BASE_DIR / 'vehicle-base.svg'` 与 `staging / BASE_DIR / 'vehicle-base.png'`。
     - 写入 `staging / BASE_DIR / 'vehicle-base.placements.json'`。
     - `manifest['placements']['file'] = f'{BASE_DIR}/vehicle-base.placements.json'`。
3. **图案生成逻辑**：
   - 遍历 `LABELS`（或 `CATEGORY_DIRS`），仅当 `entries`（图案记录列表）非空时创建对应子目录：
     `target_dir = staging / CATEGORY_DIRS[name]`
     `target_dir.mkdir(parents=True, exist_ok=True)`
   - 写入 SVG / PNG 文件到 `target_dir`。
   - 记录相对路径：
     `rel_svg = f"{CATEGORY_DIRS[name]}/{stem}.svg"`
     `rel_png = f"{CATEGORY_DIRS[name]}/{stem}.png"`
     `pair = [rel_svg, rel_png]`
     `files.extend(pair)`
     `pattern_records.append(dict(entry, files=pair))`
   - `manifest['categories'][label]['files'] = files`。
4. **文件完整性哈希清单（`artifact_sha256`）**：
   - 将原先仅扫描顶层 `staging.iterdir()` 改为递归扫描：
     ```python
     manifest['artifact_sha256'] = {
         p.relative_to(staging).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
         for p in staging.rglob('*') if p.is_file() and p.name != '复核记录.json'
     }
     ```
   - 字典的 key 统一为 POSIX 格式的相对路径（如 `"整车底图/vehicle-base.svg"`、`"彩条/左视图_彩条_01_车名.svg"`）。
5. **事务与备份保障**：
   - 整个过程在 `staging` 临时目录中完成后，原子 `rename` 为目标目录。
   - 若目标目录已存在，整体移动备份至 `.review_history/{车名}/{token}/`，目录树结构完整归档。

### 3.2 下游与复用兼容性
1. **`review_gallery.py` 恢复逻辑（`_restore`）**：
   - 读取 `{output_root}/{车名}/复核记录.json`，根据 `selected_ids` 和 `patterns` 恢复选择状态，不强依赖本地绝对路径。
   - 移除 `entry.pop('files', None)` 行为保持不变。
2. **槽位定位协议（`core/placements.py`）**：
   - 槽位中引用的 `files` 字段自动带子目录相对路径（如 `["彩条/xxx.svg", "彩条/xxx.png"]`），消费者使用 `folder / f` 能正确定位到实际文件。

### 3.3 测试用例与文档更新
1. **测试用例适配**：
   - `test_reviewed_exports.py`：
     - 更新顶层目录结构断言（包含各子目录与 `复核记录.json`）。
     - 更新 `artifact_sha256` 相对路径断言。
     - 验证各分类目录中的文件存在性与 PNG 透明度。
     - 验证未选类别不产生对应子目录。
   - `tests/test_paint_layers.py`：
     - 更新 `vehicle-base.svg` 路径至 `folder / '整车底图' / 'vehicle-base.svg'`。
     - 更新 `artifact_sha256` key 为 `'整车底图/vehicle-base.svg'`。
   - `tests/test_vehicle_placements.py`：
     - 更新 `vehicle-base.placements.json` 路径至 `folder / '整车底图' / 'vehicle-base.placements.json'`。
     - 验证 `data['slots'][...]['files']` 相对路径有效性。
   - `tests/test_directional_exports.py`：
     - 适配文件名与相对路径前缀断言（例如 `Path(filename).name.startswith(...)` 或在对应分类目录下核验）。
   - `tests/test_source_regions.py`、`tests/test_review_body_separation.py`、`tests/test_text_logo_separation.py`、`tests/test_emblem_logo.py`：
     - 检查并适配涉及导出文件路径读取的断言。
2. **文档同步更新**：
   - `docs/vehicle-paint-recoloring.md`
   - `docs/vehicle-placement-integration.md`
   - `docs/stripe-view-exports.md`
   - `ui_README.md`
   - `README.md`

## 4. 验收标准
1. 运行 `export_reviewed` 时，车名目录下清晰分类存放于 `整车底图/`、`车身/`、`车漆/`、`彩条/`、`文字/`、`logo/`、`门徽/`。
2. `复核记录.json` 位于车名根目录，`artifact_sha256` 与各分类 `files` 正确记录相对子目录路径。
3. 全量测试通过，没有任何测试因找不到文件或旧路径断言而失败。
