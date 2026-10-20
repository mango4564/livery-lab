# FS 矩形车窗漏读修复

2026-10-19，在已有未提交修改的工作区验证。本次只修改 `core/fs_geometry.py`，新增 `tests/test_fs_rectangles.py` 及本说明。

3261129 的尾部玻璃与黑色窗带使用 ARectangle，旧扫描器未读取该类。增加零圆角矩形解码，保留原填充、渐变、源顺序，矩阵仅应用一次；未确认的非零圆角不按普通矩形猜测。

真实 FS 对照：场景增加 10 个可见矩形，包含两侧尾部的 3 个渐变填充矩形（一个矩形覆盖多块窗框）。全场景及导出 PNG 已人工查看；导出 SVG 的 3 个玻璃路径均保留渐变引用，28 个导出文件的摘要均与清单一致。原 FS 摘要由导出流程在前后核对，原下载成果保留，修复成果另存。

验证命令：

```powershell
python -X utf8 -m unittest tests.test_fs_rectangles test_fs_geometry test_fs_gradients test_fs_display -v
python -X utf8 -m unittest test_components test_reviewed_exports tests.test_directional_exports tests.test_source_regions tests.test_paint_layers -q
git diff --check
```

共 59 项测试通过，差异空白检查通过。未调用 AI 接口，未重新打包 EXE。完整本机对照输出位于 `.local/tasks/20261019_missing_glass/`，不提交原 FS 或完整场景 JSON。
