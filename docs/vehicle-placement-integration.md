# 车辆底图与历史图案槽位对接协议

更新：2026-10-20。协议：`livery-lab.vehicle-placements`，版本 `1`。

本文交付给编辑工具开发者。本工具负责提取、人工复核及导出；编辑工具负责读取协议和放置用户上传的素材。本期提供位置与尺寸建议，不包含编辑工具、图案变形或遮挡重建算法。

## 1. 导出时机与文件

用户逐类确认车身、车漆、彩条、文字、logo、门徽后，执行现有“导出成品”，有已选车漆时输出：

| 文件 | 内容与读取入口 |
| --- | --- |
| `整车底图/vehicle-base.svg` | 车漆和结构车身；根 `metadata` JSON 内的 `placements` 字段 |
| `整车底图/vehicle-base.png` | 同一底图的透明栅格预览；PNG `iTXt` 关键字 `livery-lab.placements` |
| `整车底图/vehicle-base.placements.json` | 独立槽位协议，UTF-8；内容与上述两处反序列化后的对象一致 |
| `复核记录.json` | 现有复核记录，位于车名根目录，包含 `placements` 摘要；`artifact_sha256` 包含上述文件的相对路径 SHA-256 |
| 各图案 SVG/PNG | 按类别分别保存在 `车身/`、`车漆/`、`彩条/`、`文字/`、`logo/`、`门徽/` 子目录，槽位的 `files` 指向带子目录的相对路径 |

SVG 与 PNG 都可以独立携带槽位信息；建议以 SVG 作为可编辑底图，独立 JSON 作为通用交接入口。PNG 文本内容不参与像素绘制，需要解析文件字节中的 `iTXt`，普通 `<img>` 或 canvas 不会自动给出这份 JSON。PNG 的 iTXt 使用 zlib 压缩，解码器需要支持压缩文本与 UTF-8。

六类未全部确认时不会发布该协议。车漆明确不存在时沿用旧行为，不生成 `vehicle-base.*`。有车漆、四类图案均不存在时仍生成底图和协议，`slots` 为空数组。提取阶段的候选 base 不代表最终模板，不能代替复核成品。

导出采用现有暂存、校验、替换及历史归档流程；槽位或 PNG 元数据写入失败时，不覆盖上一份成品。无需重新抠图推断位置，所有边界来自本次最终复核选中的原始矢量对象。

## 2. 顶层协议

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `schema` | string | 固定 `livery-lab.vehicle-placements` |
| `schema_version` | number | 当前为 `1`；不支持的版本应停止自动放置 |
| `template_id` | string | 本次模板内容标识；校验三种载体是否属于同一模板，不是车型 ID |
| `source_sha256` | string | 原 FS 文件摘要；同一源文件的不同复核结果仍可能有不同模板 |
| `base_content_sha256` | string | **嵌入槽位之前**的底图 SVG 内容摘要，用于模板身份计算；不是最终 SVG 文件摘要 |
| `human_reviewed` | boolean | 当前为 `true`，指分类和图案选择经过人工复核，不表示方向或推荐布局经过人工逐项确认 |
| `physical_scale_verified` | boolean | 当前为 `false`；所有尺寸均不能作为毫米或施工尺寸 |
| `canvas` | object | 画布与坐标映射，见下一节 |
| `regions` | array | 原稿中已标注的车辆视图区域；每项有 `id`、`name`、`source_view`、`bbox_source`、`bbox_svg` |
| `slots` | array | 彩条、logo、文字、门徽的历史图案槽位 |
| `limitations` | string[] | 当前包含方向未推断、遮挡未重建、不自动关联跨视图三项限制 |

`template_id` 按协议内容和底图内容生成，不包含导出时间。同样内容重复导出保持稳定；底图、图案成员、区域或图案文件引用发生变化可能产生新 ID。不要将该 ID 当作跨版本永久车辆编号。

独立 JSON 与底图应整组保存。多载体同时提供时先核对 `template_id`、`schema_version` 和 `source_sha256`，冲突时要求重新选择配套文件。完整导出包可利用 `复核记录.json.artifact_sha256` 校验最终文件；不要用 `base_content_sha256` 校验最终 SVG。

## 3. 坐标与尺寸约定

所有协议中的 `bbox_*` 均为 **`[x, y, width, height]`**，不是 `[x0, y0, x1, y1]`。原稿、底图坐标可以为负数，不要提前取整或截断。

| `canvas` 字段 | 含义 |
| --- | --- |
| `bbox_format` | 固定 `xywh` |
| `svg_viewbox` | SVG 根 viewBox 的 `[minX, minY, width, height]`，已包含导出留白 |
| `svg_size` | SVG 根视口 `[width, height]` |
| `png_size` | 实际导出的 PNG 像素 `[width, height]` |
| `source_to_svg` | 源坐标到 SVG 根用户坐标的六参数仿射矩阵，含 Y 轴翻转 |
| `svg_to_png` | SVG 根用户坐标到原始 PNG 像素坐标的六参数矩阵，含缩放、居中留白及偏移 |

六参数顺序遵循 SVG/Canvas 的 `[a,b,c,d,e,f]`：

```text
x' = a*x + c*y + e
y' = b*x + d*y + f
```

`bbox_source` 处于 FS 页面坐标，Y 轴向上，已考虑原对象自身变换。`bbox_svg` 位于 SVG **根用户坐标**，Y 轴向下；`bbox_png` 位于导出 PNG 的左上角像素坐标，Y 轴向下。三个框描述同一历史图案。

优先直接使用 `bbox_svg` 或 `bbox_png`，无需再次应用源对象 matrix。特别注意：把新素材放在 SVG 根下的独立 overlay 组中时可以直接使用 `bbox_svg`；放入 `#source-coordinate-system` 时必须转换回源坐标，不能重复翻转 Y 轴。

PNG 使用等比渲染，并有高度上限及尺寸向上取整。**不能**直接用 `png_width / viewBox_width` 和 `png_height / viewBox_height` 各自缩放，也不能把分类 PNG 的左上角当作底图坐标原点。协议给出的 `svg_to_png` 已按实际渲染过程计算。编辑工具若重新裁切、旋转或改变底图画布，需要同步更新映射；仅修改 CSS 显示大小时，保留原始坐标，在显示层应用同一变换即可。

## 4. 单个槽位

| 字段 | 含义 |
| --- | --- |
| `id` | 根据类别、原始成员和来源区域生成的槽位 ID；只在同一源文件范围内使用 |
| `category` | `stripes` / `logo` / `text` / `emblem` |
| `label` | 对应中文类别或 `logo`；门徽单独文件的名称仍可能使用“徽章” |
| `source_ids` | 最终图案包含的原 FS 对象 ID，按源顺序排列 |
| `source_region`、`region_name` | 所属原稿区域 ID、名称，无法归属时为 `null` |
| `source_view` | 左/右/前/后/顶/底视图等来源标签；可能为“待确认视图”“混合视图”等 |
| `bbox_source`、`bbox_svg`、`bbox_png` | 三种坐标系中的历史图案边界框 |
| `center_svg` | SVG 根用户坐标中的中心点 `[cx,cy]` |
| `source_order_indices` | 对象在解析后源绘制列表中的零基序号；仅作参考，不是编辑器图层编号 |
| `files` | 对应历史图案文件名，相对于当前导出目录；不包含真实本机绝对路径 |
| `placement_status` | `suggested` 或 `requires_confirmation`，见下文 |
| `confirmation_reasons` | 需要确认的原因代码数组 |
| `fit`、`align` | 本期固定为 `contain`、`center`，表示等比完整放入并居中 |
| `rotation_degrees` | 本期为 `null`，**不表示已确认角度为零** |
| `orientation_status` | 本期为 `not_inferred`，不根据宽高框推断斜向或阅读方向 |
| `mirror` | 本期为 `false`，不自动镜像素材 |
| `clip_to_historical_shape` | 本期为 `false`，不按旧图案轮廓裁剪新素材 |
| `related_slot_ids` | 与本槽位共享源对象的其他槽位 ID，通常为门徽及其内部 logo |

一个复核图案生成一个槽位；跨已知来源区域的图案沿用现有逻辑按区域拆开。**不会自动把一个人工合并的图案拆成多个独立 logo，也不会猜测同车型的左右两侧属于一组。** 用户希望分别替换的图案应在复核阶段分别添加。

`suggested` 表示图案记录有明确区域、六个方向之一且边界非退化，可作为默认位置候选；不保证新上传素材适合原图案方向或形状。`requires_confirmation` 的原因包括：

| 原因代码 | 含义 |
| --- | --- |
| `legacy_category_group` | 旧调用仅保存类别整体成员，没有逐图案记录，可能合并了多个位置 |
| `region_unassigned` | 无法唯一归入来源区域 |
| `view_unresolved` | 不是明确的左、右、前、后、顶、底方向 |
| `degenerate_bounds` | 宽或高为零，不应执行自动尺寸适配 |

分类与图案复核并不等于已验证图案内容、角度或文字基线。本期所有类别都提供居中等比建议；文字基线、倾斜彩条、透视拉伸与曲面适配均未推断。

## 5. 最小读取及放置示例

SVG 可直接在浏览器端读取；示例只返回数据，不实现编辑界面：

```javascript
function readVehiclePlacements(svgText) {
  const doc = new DOMParser().parseFromString(svgText, 'image/svg+xml');
  if (doc.querySelector('parsererror')) throw new Error('SVG 解析失败');
  const metadata = Array.from(doc.documentElement.children)
    .find(node => node.localName === 'metadata');
  if (!metadata) throw new Error('缺少导出元数据');
  const data = JSON.parse(metadata.textContent).placements;
  if (data?.schema !== 'livery-lab.vehicle-placements' || data.schema_version !== 1)
    throw new Error('没有槽位信息或协议版本不受支持');
  return data;
}
```

PNG 在服务端可用 Pillow 读取，或由前端使用支持压缩 iTXt 的 PNG 解码器处理：

```python
import json
from PIL import Image

with Image.open('vehicle-base.png') as image:
    placements = json.loads(image.info['livery-lab.placements'])
```

独立 JSON 直接按 UTF-8 解析即可。对协议还应检查尺寸、数值和所选槽位是否有效，避免旧文件或错误配套导致误放置。

以下算法把正宽高的素材放入选定槽位，输出位置采用 SVG 根坐标；PNG 工作流将 `bbox_svg` 换成 `bbox_png`：

```javascript
function containInSlot(slot, assetWidth, assetHeight) {
  const [x, y, width, height] = slot.bbox_svg;
  if (![x, y, width, height, assetWidth, assetHeight].every(Number.isFinite) ||
      Math.min(width, height, assetWidth, assetHeight) <= 0)
    throw new Error('无效尺寸，需要人工放置');
  const scale = Math.min(width / assetWidth, height / assetHeight);
  const w = assetWidth * scale, h = assetHeight * scale;
  return {x: x + (width - w) / 2, y: y + (height - h) / 2, width: w, height: h};
}
```

素材宽高应使用有效内容边界；透明 PNG 自带的大量空白边距会影响视觉大小，是否去除边距由编辑工具决定。该示例不包含角度恢复、裁切或变形。

## 6. 编辑工具应处理的语义

1. 先由用户选择或工具确定上传类别，再按当前 `source_region` 筛选对应类别槽位；同一方向可能有多辆车或多个区域，不要只按“左视图”匹配全页。
2. 唯一、可建议的槽位可用于初始摆放；多个候选或 `requires_confirmation` 记录需要用户选位置。没有槽位时保留手动放置能力。
3. `related_slot_ids` 表示历史对象共享，**不表示两个槽位都应填入同一新素材**。例如完整门徽可能已包含中央 logo，需避免再叠加一份 logo。
4. `files` 指向历史独立图案，仅供查看或替换关联，文件尺寸与底图画布不同。不得按独立 PNG 的像素尺寸原点直接叠回底图。
5. 本期没有车窗、轮胎、结构线的遮挡蒙版，也没有新素材应插入的精确图层位置。`source_order_indices` 无法单独重建任意新图案与底图的遮挡关系；编辑工具应允许人工调整层级，不能承诺放置后遮挡完全正确。
6. PNG 是栅格预览，即使携带槽位元数据也没有车漆矢量层。继续做漆面转换时，应读取 SVG 的车漆路径和渐变。原图层约定见 [车漆交接文档](vehicle-paint-recoloring.md)。

## 7. 对接验收

- 同一次导出，独立 JSON、SVG `metadata.placements`、PNG iTXt 内容反序列化后相等，模板 ID 一致。
- 使用左、右侧分别存在 logo 的模板，按来源区域选槽位，不能串到另一视图。
- 在包含负坐标、不对称上下布局和高窄画布的模板上，槽位框与原始图案位置重合；检查 Y 轴翻转和 PNG 高度限制。
- 新 logo 宽高比与旧 logo 不同，完整等比放入框内；不拉伸、不默认镜像、不按旧轮廓裁切。
- 门徽与中央 logo 的关联能被识别，避免无条件重复放置；角度未知或多候选时能人工调整。
- 旧 SVG/PNG 没有槽位信息或第三方处理丢失元数据时，可读取匹配的独立 JSON，或退回手动放置；不能靠文件名猜测模板关系。

本仓库离线验证入口：

```powershell
python -B -X utf8 -m unittest tests.test_vehicle_placements tests.test_paint_layers test_reviewed_exports -v
```
