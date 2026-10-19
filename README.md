# 彩条提取工作台

本地 Windows FS 矢量提取工具，直接导出源文件曲线，不依赖 Flexi 界面操作。
当前版本为独立曲线识别版：新提取任务不读取、查找或匹配任何参考库。

## 运行与使用

打开 `release/独立曲线识别版_20261019/彩条提取工作台/彩条提取工作台.exe`，
或运行根目录 `launch_latest.ps1`。保留程序旁的 `_internal` 文件夹。
源码方式：安装 `requirements.txt` 后执行 `python app.py`。

1. 添加 FS 文件或文件夹，设置输出目录与并发任务数。
2. 在 AI 配置中设置兼容 Chat Completions 的图片模型、接口地址及本机密钥。
3. 勾选五类提取后，识别车身、彩条、文字、logo、门徽；取消勾选则仅提取彩条。
4. 对照原稿检查结果，可按对象 ID 补选、排除，也可进入五类复核画廊确认图案。
5. 导出 SVG 与透明 PNG。重跑和人工修正创建独立版本，保留旧版本。

五类候选包括原生组合、独立源曲线（SourcePath）与空间组合（SpatialPaths）。
空间组合只是供视觉模型判断的提议，不能凭同色或相邻直接认定为彩条。
单条候选仍可选，可组合多色设计或排除结构曲线；规则不绑定文件名、固定颜色或对象编号。
AI 只选择已提供的源对象 ID，不重画曲线。未知、缺失或不确定类别保持为空并要求复核。

本机配置 `settings.json` 与加密密钥 `.api-key.dpapi` 不提交 Git，密钥环境变量优先。
“测试连接”只验证文字请求，不代表真实图片请求一定可用。可以在配置窗口调整是否使用系统代理。
图片请求采用流式响应，完整收到正常结束标识后才校验 JSON，断流、截断及未知 ID 均拒绝。
临时网络或可重试 HTTP 错误在总超时内最多尝试三次；不会自动换模型或降级识别。
实际任务并发不超过本批文件数，每个任务使用独立工作进程。

## 命令行与验证

```powershell
python -X utf8 cli.py "素材.fs" --output "output" --ai --components
python -X utf8 cli.py "素材目录" --output "output" --ai -j 4
python -X utf8 -m unittest test_components test_engine_transactions test_batch_concurrency -v
python app.py --self-test
```

旧参数 `--reference-directory` 仅为命令行兼容保留，识别时忽略。
`reference_match.py`、`reference_library/` 和参考验收工具保留作历史研究与离线回归，
不参与当前提取流程。

已验证本机案例 `3261754.fs` 的三条未编组彩条可由源码和打包版各自独立识别。
详见 [独立识别验收](validation/independent_stripes_20261019/README.md) 和
[接口与并发历史摘要](validation/vision_transport/README.md)。
这些是已知案例回归，不代表未知素材准确率。历史参考匹配结果保留在
`validation/reference_exports/` 与 `validation/reference_audit/`，只说明当时的版本。

## 限制

- 当前仅处理第 1 页，复杂布尔操作、裁剪、部分字体、纹理及其他原语尚未全面支持。
- 候选有数量和几何筛选限制，拆散、细小或未解码的对象可能遗漏。
- 模型置信度未经准确率校准，仍需检查多选与漏选。
- SVG 使用源曲线坐标，但物理比例未验证，不能直接据此确定下料尺寸。
- 复核导出界面有一项 Tk 线程清理测试异常，详见独立识别验收说明。

## 开发与文件管理

AI 修改先阅读 [AGENTS.md](AGENTS.md)，遵守 [仓库文件管理规范](docs/repository-management.md)。
源码和有效测试保留现有位置；新增文档、脚本、测试分别进入 `docs/`、`scripts/`、`tests/`。
临时结果放 `.local/tasks/`，应用结果放 `output/`，构建中间文件放 `build/`，发布包放 `release/`。
清理记录见 [2026-10-19 清理记录](docs/maintenance/2026-10-19-cleanup.md)。
