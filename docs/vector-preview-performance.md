# 矢量预览性能

## 调查范围

2026-10-20，针对车型切换、复杂车身缩放和拖动调查。基于已有未提交工作区进行优化，保留原有修改；本次不改变 FS 原件或导出数据。

官方对照目标为用户指定的 FlexiSIGN-PRO 8.1v1 安装目录。使用 REA / Ghidra 静态分析，不运行或修改官方程序。静态代码能确认绘制机制，不能测出官方实际帧率，也不能证明每一项机制对流畅度的贡献。

## 已确认的开销

原 `VectorImage.render` 为补偿 Skia SVGDOM 不支持的 `non-scaling-stroke`，每次缩放修改所有 `data-fs-hairline` 节点，再序列化整份 XML 并重新解析 SVGDOM。此工作发生在 Tk 绘制回调内。16 ms 合并事件只减少回调次数，不能缩短一次绘制占用。

样本为已有本地 `3261588.fs.svg`：7,750,411 字符、4,162 条 path、3,055 条 hairline；1000×650 视口，原实现初始化约 121 ms，四次连续缩放约 150 / 125 / 115 / 122 ms。完整样本留在被忽略的本地任务目录，不复制进验收资料。时间包含矢量视口渲染及像素转换，不代表官方对照帧率。

原实现已经限制栅格分配为可见视口，且有文档后台加载、最近三份文档缓存和过期加载结果检查。这些能力继续保留。之前的 [界面响应验收](../validation/ui_responsiveness/README.md) 使用隐藏窗口，明确未覆盖可见复杂图的最终绘制，因此不能据此判断缩放流畅。

## 官方静态证据

`fscore.dll` SHA-256：`bcddb4912f9b044f1ba6b0350d7145f85983ba0a376b9e4714558bf12197471d`。本次 REA 打开结果与仓库历史证据的目标摘要一致。

- `AObject::draw`，`0x10032dc0`：存在无效区域时，先求对象包围盒并 `IntersectRect`，不相交则返回，再进入结果绘制。证据 `ev_eccde09e5e877b85b6d87e6ae5907d7730dba1ebaa9d57e9cb7e38adc73d0d11`，见 [objectdraw.json](../research/native_rendering/objectdraw.json)。
- `APath::draw`，`0x1003a550`：路径同样进行包围盒相交检查。证据 `ev_9029060fd4320e34c1713dc36bcf17bc04d0bed6ca2a78ce1dfd24b664e9c144`，见 [path_draw_geometry_rea.json](../research/native_rendering/path_draw_geometry_rea.json)。
- `ADevice::initPenBrushColor`，`0x100994b0`：细轮廓使用零逻辑宽度的原生 GDI cosmetic pen。证据 `ev_c26c722c7c43f3b4795104f51f624d38a8af4284be90cdcd18d5acbb32f9a012`，见 [device.json](../research/native_rendering/device.json)。此机制支持在屏幕上保持细线，无须通过改写 SVG 实现。
- 本次 `App2.exe` 名称搜索发现 MFC 绘制和 GDI `BitBlt` 导入；证据 `ev_1a05a5231f7e1b77099335830967698726cd490ae1f187c511aed8dd0bfbe133`。仅有导入不能证明某个缩放流程实际调用了它，不能据此认定 GPU 加速或缓存策略。

这些证据支持采用“保留已解析几何、屏幕细线、减少无效区域绘制”的设计方向；本项目具体优化以自己的像素回归和运行测量为依据。

本次继续反编译得到的调用路径：

| 函数 | 静态观察 | Evidence ID |
| --- | --- | --- |
| `ADocView::zoomArbitrary` `0x1005a9b0` | 更新缩放和锚点、调用 `InvalidateRect`、更新滚动位置 | `ev_1a6df4b978e9cd07224363d1e9ed157581e489dca4cdd2d4f89d402f54abd19b` |
| `ADocView::OnDraw` `0x10058d70` | 将更新区域转到绘图坐标，必要时 `redrawRegion`，之后 `copyRegion` | `ev_325246a0c214ce828232349b014f043a2bc2f0271f98ba4b8eb6c9946abc0e9b` |
| `ADeviceMgr::redrawRegion` `0x100530e0` | 为目标设备设置裁剪区域；有离屏设备时向离屏设备绘制，再按条件复制 | `ev_d045f11ab87d4a0ef14393527ff83a968ee722fb0e62a4628570ea68e19c285b` |
| `ADeviceMgr::resizeBuffers` `0x10052c70` | 比较尺寸和相交区域；部分分支同步坐标并复制原缓冲内容，返回待重绘范围；尺寸变化时创建 `AOffscreenDevice` | `ev_c7c616d17a04d1be3c602fea5d36e8772a4c7901b8db7546941b32f47f257ccb` |
| `ADeviceMgr::copyRegion` `0x100538c0` | 缓冲有效时复制指定区域；不满足条件时退回 `redrawRegion` | `ev_5fcb7f2288fbc93076ab8790c4e65b87722ae4fa31a3e53cc543aaddf9006baf` |
| `AObject::getFinalResult` `0x10031250` | 读取对象内结果指针；为空且条件满足时才构建并保存结果 | `ev_f0334c40e4eff4c74ad3c1cb1fe6aec2bec9a3b05855b3137ab42e392a167036` |

精简反编译证据见 [performance.json](../research/native_rendering/performance.json)。`resizeBuffers` 有两处 Ghidra terminal-flow 边界提示，未解开的虚调用、返回结构和参数恢复仍有不确定性；因此这里只描述可见分支，不声称已恢复完整缓存算法。`getFinalResult` 的条件被反编译为 `unaff_retaddr`，不据此推断原始参数语义。以上未包含官方界面的动态帧率、线程调度或 GPU 跟踪。

## 本项目实现

- 将可识别的 FS 预览 SVG 按每批 128 个节点录制为 Skia Picture，保存路径、填充和绘制顺序；用 RTree 为离屏命令提供裁剪索引。细轮廓使用 Skia 零宽画笔，缩放无需重写 SVG。渐变等普通对象通过原导入器录制，保留 viewBox 和对象变换。成功后释放重复的 SVGDOM/XML 存储。
- 遇到未支持的 SVG 样式、剪裁或其他语法，继续使用原导入器，避免静默丢失内容。兼容路径仍可能较慢。
- 矢量精确渲染交给后台线程；Tk 主线程只呈现图片。请求队列与完成队列各至多保留一个最新项目，丢弃过期结果，关闭窗口时结束工作线程。
- 操作中先变换上一张精确帧，停顿 80 ms 后后台重绘当前比例的精确矢量结果，避免连续操作时与耗时原生调用争用 Python GIL。临时帧可能短暂变软或在新露出区域显示空白，停止后由精确帧补齐。此缓存不会写入导出成果。
- 棋盘格按尺寸做有界缓存；更新预览图片时保留并置于手动选择框下方。同一矢量对象共享给多个窗口时串行访问其渲染状态。

优化将工作转移到首次加载：4,162 路径样本初始化从约 109 ms 增至 383 ms，首次矢量帧仍需约 125 ms；51,794 路径样本初始化约 2.63 秒，首次矢量帧约 226 ms。主界面已有后台加载与最近三份文档缓存可摊薄此成本。不能将约 5 ms 的后续帧中位数理解为首次切车延迟或稳定 200 FPS；最终显示还包含 Tk 上传、合成和调度。超大图首次呈现仍可能有停顿，尚未达到所有场景均与官方同等流畅。

可运行离线基准，默认不访问网络或收费接口：

```powershell
python scripts/benchmark_vector_preview.py INPUT.svg --output .local/tasks/YYYYMMDD_vector_performance/benchmark.json
```

加 `--baseline PATH_TO_OLD_VECTOR_PREVIEW.py` 可测量旧实现。脚本报告初始化、首次与后续矢量帧、实际映射 Tk 窗口中的 10 ms 心跳、呈现时间，以及最终精确帧是否就绪。验收结果见 [validation/vector_performance](../validation/vector_performance/README.md)。

## 问题覆盖

| 问题 | 状态 |
| --- | --- |
| 官方用了哪些可借鉴机制 | 已用静态证据确认结果复用、离屏缓冲和区域裁剪；实际执行频率未观测 |
| 本项目复杂缩放为何卡顿 | 已确认缩放重解析与主线程绘制开销；大图还受原生调用占用 GIL 影响 |
| 优化是否有效且保留精确矢量 | 两个真实样本及像素/交互回归已验证；临时缓存最终由精确矢量帧替换 |
| 首次切车是否立即完成 | 未实现；首次编译仍需时间，后台加载和最近文档缓存继续发挥作用 |
| 所有车型是否达到官方体验 | 未证实；未做官方动态帧率对照，未知 SVG 语法和特殊效果仍有边界 |
