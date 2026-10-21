# FS CMYK 颜色恢复验证

日期：2026-10-21。工作区保留了任务开始前已有的 components.py、vision.py、候选门禁文档及测试改动。本次修改 core/fs_attributes.py，新增 tests/test_fs_cmyk_colors.py；未提交 Git。

## 根因和修复

输入为用户提供的 CK-6122 / 3231416 FS，SHA-256：`43a972dfd87f9defe28d222c19d80c8d7e08c3d03ec9f2578a795314bae04b54`。
ACMYKColorItem 的公共 RGB 缓存全部为零，旧实现误将其当作黑色。现在按文档内类名识别 CMYK，读取 field 1 的 C/M/Y/K 四字节并转换；填色与画笔共用修复。保留原始 CMYK 通道，标记 `source_cmyk_approximation`，畸形数据标记未知，不回退为黑色。RGB、专色和渐变仍走各自解析。

官方 fscore.dll SHA-256：`bcddb4912f9b044f1ba6b0350d7145f85983ba0a376b9e4714558bf12197471d`。REA / Ghidra 12.1.4 静态证据：

- `ACMYKColorItem::flexiSerialize`，0x1000d820，Evidence `ev_a7d0031dc1c7da561a4fbe384f08345c806afbd40c4f649e37379b5bc27de94e`：field 1 顺序读写 this+0x40 至 this+0x43 四个字节，加载后更新输出颜色。
- `ACMYKColorItem::getColor`，0x1000d900，Evidence `ev_5aab6cf59952738f0e466ecbb866230ca8d70a4647e760639b723026c4162363`：取上述四通道交由 AColorMgr::icmDummyConvert 转换，未使用公共 RGB 缓存。

这证明数据来源和读取路径，不证明本项目公式与官方色彩管理逐像素一致。当前使用 `round((255-C)*(255-K)/255)` 等近似，不依赖或分发官方 ICC 文件；官方截图的色调可能有差异。未扩展字体、蒙版及其他尚不支持的图形语义；原始曲线不修改。

## 验证结果

- `python -X utf8 -m unittest tests.test_fs_cmyk_colors test_fs_display test_fs_gradients -v`：14 项通过。覆盖 CMYK 原色、黑白、混合墨量、畸形记录、零缓存、RGB 保持、32/64 位外观记录、填色/画笔及 SVG。
- `python -X utf8 -m unittest discover -s . -p "test_*.py" -v`：314 项通过，44.509 秒，无真实 AI 调用。
- 原文件整页解析 26,742 个组合后的可绘制对象，381 个填色与旧逻辑不同。整页 SVG/PNG 已实际渲染并目视检查，红色车顶、文字、标志和橙色彩条恢复。
- 离线六类入口实际运行成功。类别保持待人工复核，不宣称 AI 分类完成。另用 152 个红/橙源对象作临时导出测试，实际调用引擎的六类导出渲染函数；SVG 含两色，PNG 检测到 176,353 个红色像素。测试选择仅验证颜色，不作为完成的类别成果。
- `python -X utf8 scripts/build_windows.py --label CMYK颜色修复版` 成功；源码、打包 EXE、配置继承后的 EXE 自检及 ZIP 完整性检查通过。发布标识 `CMYK颜色修复版_20261021_142914_743b2c`。
- 新 EXE 使用 `--extract` 对同一 FS 离线实测成功，manifest 中有 349 个 #e90000 对象、13 个 #ff7b00 对象。此入口保留原始叶对象，数量与整页组合结果不同。

完整日志、原文件解析结果及对照图保留在被忽略的 `.local/tasks/20261021_fs_cmyk/`；构建日志沿用打包工具固定的 `.local/tasks/20261021_packaging/`。未删除用户成果、素材或旧发布包。未调用真实 AI 接口，未自动提交或推送。

## 后续：左侧车身灰色占位修复

用户指出第一版对照图左侧车身仍有灰块。这属于另一处解析错误：明确保存为 `00000000` 的空 ABrush 被标为未知，SVG 随后将未知填色画成 `#8b98a8`。现在只有这种明确的空指针记录映射为 `fill=none` / `source_no_fill`；缺失记录和不支持的外观仍保留未知。独立画笔不受影响。没有按对象 ID 或坐标将车身硬编码为白色。

使用同一官方 DLL 重新静态核验：

- `AObject::getDrawPenAndBrush` 0x1002fee0，Evidence `ev_0dd543737e55e603042a8767d25000c352753d8a5e89c343be5e2f8d7c7979ae`：普通显示 case 1 在画笔、颜色覆盖、画刷均空时返回，没有从组继承填色或添加默认灰色。
- `APath::draw` 0x1003a550，Evidence `ev_e2670a17f7535b6a807a956bcdbc137f63b4aa074a58cf1cb1da5d3bc3581fdd`：从 this+0x20 取得画刷，颜色覆盖初始为空；没有画刷及颜色覆盖时跳过填充。不是官方运行时观测，也不宣称完整实现所有效果。

验证：

- 新增 `tests/test_fs_empty_fill.py`，覆盖 32/64 位记录、独立描边、未知数据保持未知、透明/白色/红色背景叠加像素，以及复合对象不误用子路径填色。
- 定向 18 项、全量 318 项测试均通过；全量耗时 41.984 秒。
- 原文件整页 26,742 个可绘制组合对象，其中 618 个明确空填色；94 个原先画成灰色的封闭对象/复合对象恢复透明。其余为空填色的开放路径原本已不填充。当前整页没有无渐变的未知填色，SVG 不再出现诊断灰 `#8b98a8`。
- 实际渲染并目视检查 `.local/tasks/20261021_fs_empty_fill/after.png`：左侧车身主区域灰色占位消除，下方红/橙彩条保留。字体、效果、颜色管理仍存在此前说明的渲染差异，不宣称与官方截图逐像素一致。
- 重新构建 `车身填色修复版_20261021_143538_543ca6`，源码/EXE/配置继承启动自检和 ZIP 完整性检查全部通过；使用新 EXE `--extract` 处理原文件成功。打包入口读到 28,857 条源对象、690 条明确空画刷；主车身对象 306 填色为 none，红色仍存在。
- 完整对照图、日志和打包实测数据留在被忽略的 `.local/tasks/20261021_fs_empty_fill/`。未清理任何用户材料或旧包，未提交或推送。
