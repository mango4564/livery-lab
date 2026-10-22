# 车漆提取与 SVG 图层交接

更新：2026-10-19。范围依据用户最新确认：**本工具只提取与标注；同事开发的网页负责 AI 推荐色号和换色。** 此文替代调查阶段将 HTTP 服务和换色算法放进本工具的设计。

## 1. 交付边界

| 内容 | 本工具 | 同事的网页 |
| --- | --- | --- |
| 解析 FS、生成候选、AI 选择源对象 | 实现 | — |
| 车漆与车身人工复核、分层 SVG 导出 | 实现 | 读取导出文件 |
| 推荐 HEX、处理用户换色需求 | — | 实现 |
| 根据 HEX 改变主色调、保留渐变明暗 | 保留原样式供使用 | 实现换色算法 |
| 独立 Web 前端和 HTTP API | 不提供服务端 | 自行实现 |
| 同一车辆多视图同步换色 | 保留所选视图的源位置与 ID | 确认同车后统一处理 |

车漆是可喷漆的底色区域；车身是轮廓、玻璃、轮胎、灯具、接缝等结构。彩条继续独立，不归入车漆。首期外部网页采用六位 sRGB HEX，例如 `#1E5AA8`；实体色卡以后接入。HEX 不等于实体喷漆配方。

## 2. 调查依据与修复

原先只有车身、彩条、文字、logo、门徽五类，且散落候选要求含曲线 `C`、宽高比至少 1.8、高度不超过整页 30%。这会排除大面积底色和直线闭合面，单改提示词不能召回未提供的对象。

旧版三份真实 FS 离线解析结果：

| 样本 | 场景对象 | 原生候选 | 散落候选 | 提供候选 |
| --- | ---: | ---: | ---: | ---: |
| 3261042 | 2,548 | 440 | 11 | 120 |
| 3261074 | 7,824 | 970 | 36 | 120 |
| 3261129 | 20,550 | 129 | 5 | 120 |

3261042 的 `223@7291`、`226@18665`、`228@29839`、`230@37716` 四个米色填充均未进入旧候选。3261129 的 `219@15192`、`221@23069` 同样遗漏。实现新增 PaintSurface 通道，接纳大面积的已支持实色及线性/径向渐变面，独立于旧散落曲线通道；它只是候选，不按颜色、面积或文件名自动判为车漆。

默认 120 个候选分配给五个来源桶，车漆通道预留 40 个名额，其余四桶各 20 个；不足时按可用候选补足。车漆通道同时提供 PaintRegion 局部组合与独立 PaintSurface：以大底色为锚点，召回邻近同填充的顶部、后视镜等分散面，较小的顶部壳体使用局部面积门槛。组合仍需视觉判断，不按同色自动分类；玻璃、车灯、轮毂等同色结构不能直接归入车漆。候选仍有数量和面积阈值限制，遗漏时可用原稿对象点选/框选补充。

六类协议同时更新了模型返回校验、父车身扣除子对象、复核状态及导出。车漆不可与彩条、文字、logo、门徽共享同一源对象；车身父组中已归入其他类别的对象会扣除。门徽中央的标志允许同时属于门徽和 logo：门徽导出包含弧形文字及标志，logo 单独导出该标志。候选新增 DoorEmblem 完整组合及 EmblemLogo 内部图形，不再仅按弧形文字裁掉标志。其他非车身类别仍禁止重叠。合并预览中共享标志只绘制一次，归入门徽层，metadata 的 `shared_logo_emblem_ids` 标出共享源 ID，`layers.logo.source_ids` 仍保留完整逻辑归属；各类别单独文件均含自己的完整路径。保持源复合路径与 evenodd 填充，避免拆分导致孔洞丢失。

用户授权的官方工具静态调查确认 `fscore.dll` 中 `AGroup::draw` 按子对象列表顺序绘制，因此语义分组必须关注遮挡顺序。目标 SHA-256：`bcddb4912f9b044f1ba6b0350d7145f85983ba0a376b9e4714558bf12197471d`；函数地址 `0x10027c80`。没有运行或修改官方程序。[精简证据](../validation/vehicle_paint/native-group-draw.json)、[原生绘制研究](../research/native_rendering/FINDINGS.md)、[渐变研究](../research/fs_gradients/FINDINGS.md)。

## 3. 使用与文件输出

1. 源码运行 `python app.py`，勾选“六类提取”。AI 模式识别候选；关闭 AI 可手动分类。

   AI 默认等待预算为 600 秒（10 分钟），初次识别、网络重试和至多一次分类纠错共享预算；提前返回就立即继续，不会固定等待。可用本地 `settings.json` 的 `timeout_seconds` 或环境变量 `LIVERY_TIMEOUT_SECONDS` 覆盖（1–600 秒）；已有显式配置优先于默认值。

   识别候选生成、配额和排序沿用 `34bafb4` 稳定版，直接执行一次六类分类，不执行前置整车定位或逐车车漆补查。保留返回格式校验、有效方向映射转换及至多一次同模型纠错；格式错误与分类冲突共用一次纠错机会和总时间预算。
2. 打开“六类复核”，直接进入候选与原稿预览界面。AI 六类任务会把已有识别结果默认选入，并按来源区域拆分；已保存的人工选择及“无图案”优先恢复。此步骤不追加 AI 请求，手工模式不自动选入。
3. 遗漏或误选时，切换左侧对应类别，比较候选、移除不需要的已添加项，或在原稿中点选/框选。车漆需要包含待交付视图的全部底色区域，不要选中整辆车父组作为车漆。
4. 核对无误后点击右上角“确认当前方案并一键导出”，一次确认整套选择。AI 返回空数组的类别不会自动视为不存在：导出时集中询问是否无需导出；取消后可补选，也可在调整页明确选择“该类没有图案”。手工任务及异常类别仍需补选或明确标记。车漆已选择时增加 `整车底图/vehicle-base.svg` 和透明预览 `整车底图/vehicle-base.png`；各类别在同名子目录内输出各自的 SVG/PNG，门徽目录名为“门徽”。

复核页打开时，候选列表会用蓝色同时高亮与本类 AI 预选或已保存图案匹配的候选；不再显示总览页或绿色候选底色。点击左侧类别可切换查看。候选使用 Windows 常见操作：单击选一个，Ctrl＋左键追加/取消，Shift＋左键连续选择，Ctrl＋A 全选本类全部候选（包括其他页）。蓝色高亮就是当前导出选择：选中立即加入清单，取消选中立即移除，无需再点击添加。跨页和切换类别保留选择及各自来源区域、方向，右侧“当前导出图案”同步显示最终清单。来源视图下拉框修改后自动保存，不再需要点击更新。

当前导出图案列表同样支持 Ctrl/Shift＋左键多选及 Ctrl＋A 全选，可按 Delete 或点击“移除选中的导出图案”；也可使用“全选当前导出图案”按钮。移除只影响当前复核方案，不删除源文件或历史成品；手动取消本类全部选中或清空列表后，本类即不导出；AI 原本未识别到的空类别仍在导出时集中确认。

车身候选可能来自包含车漆的原生父组。复核页的车身缩略图、大图、已添加图案预览及路径数会扣除当前车漆等其他类别，和最终车身导出的扣除规则一致，并显示扣除数量。未选择类别暂用本次识别结果；用户选择或明确标记“无图案”后以用户选择为准。原始父组成员保留，以便调整分类后重新预览，不会因看过一次车身就永久删掉源对象。AI 预选仍属待复核方案，点击整体确认导出后才发布成品；预览扣除本身不代表人工确认。

`vehicle-base.svg` 只包含所选车漆和结构车身，采用同一画布与源位置；不包含单独分类的彩条、文字、logo、门徽。各类别单独文件自动裁取本类边界，不能直接按 PNG 像素尺寸叠放；若网页还需叠加其他类别，应读取 SVG viewBox 与坐标变换。

整车底图同时附带彩条、文字、logo、门徽的位置记录：SVG 的 `metadata.placements`、PNG 的 `livery-lab.placements` 压缩 iTXt 和独立 `整车底图/vehicle-base.placements.json` 保存一致数据。编辑工具可按原稿区域读取位置、尺寸和历史图案文件引用，协议与读取示例见 [历史图案位置对接协议](vehicle-placement-integration.md)。位置记录不参与底图绘制。

车漆明确不存在时，不生成空的 base 成品。原五类历史记录仍可打开；新增车漆必须重新确认，不自动将旧任务当成已确认车漆。修改代码会改变流水线摘要，使旧提取缓存失效。

## 4. 给网页开发者的 SVG 约定

结构示意（数值和路径仅作示意）：

```xml
<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     viewBox="0 0 100 50">
  <metadata>包含 source_sha256、human_reviewed、layers 等 JSON</metadata>
  <defs><!-- 原始渐变，每个路径引用独立定义 --></defs>
  <g id="source-coordinate-system" transform="translate(0 50) scale(1 -1)">
    <g id="layer-paint" data-layer="paint"
       inkscape:groupmode="layer" inkscape:label="车漆">
      <title>车漆</title>
      <path id="223@7291" data-source-id="223@7291"
            data-layer="paint" data-paint-editable="fill"
            fill="#dedabf" d="…"/>
    </g>
    <g id="layer-body" data-layer="body"
       inkscape:groupmode="layer" inkscape:label="车身">
      <title>车身</title>
      <path id="…" data-layer="body" d="…"/>
    </g>
  </g>
</svg>
```

| 字段/选择器 | 约定 |
| --- | --- |
| `#layer-paint` / `[data-layer="paint"]` | 车漆层/车漆对象 |
| `#layer-body` / `[data-layer="body"]` | 结构车身层/对象 |
| `inkscape:label`、`title` | 人可读中文图层名称 |
| `path[data-source-id]` | 对应原 FS 的对象；ID 可能含 `@`，不要拼未经转义的 CSS ID 选择器 |
| `data-paint-editable="fill"` | 此路径的填充属于车漆；不表示可以改动 stroke |
| `fill="#RRGGBB"` | 原始实色填充 |
| `fill="url(#fs-gradient-N)"` | 原始渐变；读取 defs 内对应色标和变换 |
| `layers.paint.source_ids`、`layers.body.source_ids` | metadata 中各类别的源对象数组 |
| `human_reviewed` | 成品为 true；候选预览为 false |
| `exportable`、`layer_status` | 成功分层时 true / verified |
| `physical_scale_verified` | 当前恒为 false；像素画布不代表毫米尺寸 |

建议用 `DOMParser` 按 `image/svg+xml` 读取，定位 `getElementById('layer-paint')` 后枚举该组内路径。使用查询选择器时可用 `querySelectorAll('[data-layer="paint"] path')`。本工具的 source_ids 只关联对象，不是网页车辆或车型 ID。

不要只修改父组的 `fill`：路径已有明确 fill，父组属性不会覆盖。实色路径由网页修改路径的 fill；渐变路径需要修改其引用定义中的 stop-color，并保留 offset、坐标、gradientTransform、gradientUnits。渐变定义按路径独立输出，修改车漆引用不会改变车身的渐变。

车漆路径可能同时携带轮廓 stroke。网页换色只处理填充/渐变色标，保留 stroke、stroke-width、d、fill-rule、transform 和其他结构路径。保留明暗的具体颜色映射由网页实现，本工具不把渐变压平成纯色。

通常 base 中车漆在前、车身在后，组内保留源对象顺序。遇到源车漆与结构交错、无法直接重组的情况，metadata 的 `rendering` 为 `source_order_references`：两个语义组位于 `defs` 中，仍包含唯一的原始 path；`source-coordinate-system` 中的 `use` 按源顺序引用这些 path。修改 `#layer-paint` 内 path 的 fill 或其渐变色标仍会作用于全部可见引用。保留 defs、use、href/xlink:href 和坐标组；不要将 defs 中的组直接搬到画布或重排 use，否则会改变遮挡。该模式用于程序化编辑，不表示在矢量编辑器中切换定义组显隐就能控制所有引用。各类别的单独 SVG 仍使用直接路径图层。

## 5. 复核与绘制顺序保护

当重组后顺序发生变化，程序在两个预览尺寸（640、1600，长边高度封顶 1600）逐像素比较源顺序与分组后的渲染。base 直接分组出现差异时，改用原序 use 引用，并再次做同样的像素比较；通过后允许发布，复核记录的 `base_rendering` 记载所用模式。两种方式都不能保持原样时，抛出 `layer_order_conflict`，事务不会覆盖之前的成品。

提取阶段 base 同样尝试保留原序引用；若检查失败则保留原序诊断预览，metadata 标为 exportable=false，使用 ordered-* 分段组。复核导出的 base 必须通过严格检查。六类合并预览因彩条等交错可能出现冲突；这不代表车漆/车身 base 一定冲突，两者分别检查。

像素对比是工程检查，不是所有缩放比例下完全等价的数学证明；仍需人工对照预览。当前仅解析第 1 页，部分裁剪、纹理、字体及原语尚未覆盖。候选中的未知填充可能以诊断灰显示；最终车漆含未知或未支持填充时会阻止发布，不能视为还原原色；无法解析的原始对象不能靠复核补画。

## 6. 验证入口

文字与 logo 同属原生父组时，WordmarkText 和 WordmarkLogo 分别提供文字子集、邻接图形子组供复核。公司名称及中英文全称归文字，标志内部构成图形的字母仍保留在 logo 内。已明确选择的 logo 源对象会从文字父组中扣除；复核缩略图、大图、手动编辑初选和最终导出采用同一扣除规则。取消 logo 分类后可恢复原父组选择。此规则依赖正确选中 logo，并不按颜色自动拆分任意曲线；门徽仍可保留中央标志，同时单独导出该 logo。

```powershell
python -B -X utf8 -m unittest tests.test_paint_layers -v
python -B -X utf8 -m unittest test_components test_engine_transactions test_reviewed_exports test_review_gallery tests.test_manual_object_selection -v
python app.py --self-test
```

新增测试覆盖大面积直线底色召回、车漆/车身互斥、实际 SVG 中文图层、源路径与渐变保留、遮挡冲突及失败导出不覆盖旧成品。真实样本的候选召回不等于 AI 自动分类准确率；本轮不调用收费模型验证。验收摘要见 [车漆分层验收](../validation/vehicle_paint/README.md)。
