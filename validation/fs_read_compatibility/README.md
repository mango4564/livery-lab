# FS 页面读取兼容验证

日期：2026-10-21。本任务保留此前颜色修复、界面、模型协议等已有工作；无素材修改、用户成果删除或手动提交。

## 复现范围与根因

用户最初指定 `3230493 面积大多色`，随后提供新窗口的完整堆栈：`decode_references` 读取颜色记录时 `KeyError: 0`。

对指定文件单独读取成功，其 SHA-256 为 `ffb6a874588dba9f5538d1e354b6384ee17b9a3d796cc234d31b777e9146b83a`。检查同批失败记录，确实报 KeyError 的是 `3240141 多色`，SHA-256 为 `78ef1eeaa2723eb7acdce763e9f9f1871b42b6c3de162849c86df1c5b6985494`：颜色引用 38 是 ALABColorItem，只有字段 10/11。旧解析器仅对 ARGBColorItem 使用字段 10，其余一律读取字段 0，因此触发与堆栈相同的错误。不能将最初指定文件的读取成功当作整批无问题。

## 修复

- `core/fs_attributes.py`：ALABColorItem 从字段 10 读取公共元数据，字段 11 读取 LAB 字节并转换为预览 RGB。L 为无符号字节，a/b 为有符号字节；保留原通道并标记 `source_lab_approximation`。不将 LAB 字节直接当 RGB，也不将零缓存当黑色。畸形通道明确标为 unsupported_lab。
- 同任务已修复 `ui.py` 的失败预览缓存：失败页占位文档不进入正常缓存，传入旧失败文档时重新解析；`core/fs_scene.py` 保留错误类型和完整 traceback，避免日志只剩“0”。这些改动被用户通过桌面自动打包的本地备份收录，最终工作区差异可能只显示后续 LAB 修复。
- 新增 `tests/test_fs_lab_colors.py` 和 `tests/test_preview_failure_recovery.py`，分别覆盖颜色模式/文档内类 ID、签名色度、黑白灰、畸形通道，以及失败预览后的真实重读和成功缓存复用。

## 原生依据和限制

本机 FlexiSIGN 8.1 的 fscore.dll SHA-256 `bcddb4912f9b044f1ba6b0350d7145f85983ba0a376b9e4714558bf12197471d`。REA/Ghidra 12.1.4 静态分析 `ALABColorItem::flexiSerialize`（0x10012bf0），Evidence `ev_8b4d12e36eff2ccda433075473fe2330372c32c131517e91eef337c58aba4d2b`，确认公共元数据用标签 10，三通道用标签 11。

STARBASE.dll SHA-256 `83d4a90af5b2e7a9d3960da09b3c88b10238df614b62176567ebb3b618a4b1e6`，导出 `CX_Lab2SRgb`（0x10019970）静态指令：L 使用 movzx，a/b 使用 movsx；D65 白点 95.04/100/108.89，线性 RGB 后使用量化 gamma 表。实现按这些观察提供不依赖专有文件的预览近似。证据不代表官方运行时截图，也不保证与所有 ICC 配置逐像素相同。

## 结果

- 定向 11 项测试通过；全量 `python -X utf8 -m unittest discover -s . -p "test_*.py" -v`：323 项通过，45.480 秒。
- 实际调用界面的 `_prepare_previews` 遍历复杂彩条目录全部 11 份 FS，所有页无错误。指定 `3230493` 整页组合后 2,371 个对象，`3240141` 为 2,998 个；均实际渲染 PNG 并检查。
- 构建 `FS读取兼容修复版_20261021_144919_9eb6fc` 成功；源码、EXE、继承配置启动自检和 ZIP 完整性检查通过。
- 新 EXE `--extract` 实测 `3230493` 和 `3240141` 成功，分别读取 3,850 / 5,027 个原始叶对象（与整页组合后的数量不同），均为待复核状态。另执行 `3240141` 离线六类入口成功。未调用真实 AI 服务，不宣称 AI 分类或成品审核完成。

完整数据、原生指令、两文件渲染及测试日志保留在被忽略的 `.local/tasks/20261021_fs_page_read/`；构建日志使用工具规定的 `.local/tasks/20261021_packaging/`。原文件、此前导出和旧发布包均保留。
