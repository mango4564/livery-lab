# 多稿件 AI 提取对照

`scripts/benchmark_ai_extraction.py` 对同一组 FS 比较：当前分批 Qwen 流程、结构化局部复核 Qwen、现有 Gemini 流程。它是独立验收工具，不改变应用默认配置，不自动提交或打包。

## 运行

本机模型和凭据沿用被忽略的 `.local/ai-profiles.json`，格式见 [模型切换说明](ai-profile-switching.md)。脚本默认只生成原稿预览和冻结计划，不发请求。

```powershell
python -X utf8 scripts/benchmark_ai_extraction.py --source '原稿A.fs' --source '原稿B.fs' --output '.local/tasks/20261021_qwen_holdout'
```

检查选稿后显式执行真实接口测试：

```powershell
python -X utf8 scripts/benchmark_ai_extraction.py --plan '.local/tasks/20261021_qwen_holdout/plan.json' --execute
```

中断后用同一命令继续。已完成的分类调用按源文件、候选成员、图片字节、提示、模型、路由和参数指纹复用；发现输入改变即拒绝复用。应用基线调用另包含业务实现哈希。原始配置只在内存中读取，缓存不保存密钥或接口地址。失败调用的重试记录仍保留，不能将续跑当作一遍无失败运行。

只离线汇总已经完成的结果：

```powershell
python -X utf8 scripts/benchmark_ai_extraction.py --plan '.local/tasks/20261021_qwen_holdout/plan.json' --summary-only
```

## 协议与结果

- 每份首轮都使用同源 120 候选；各方法使用自身决策产生后续补查池，因此是完整工作流对照，不是只改变单项参数的消融实验。
- Qwen 基线固定 12 个一批、简洁提示、temperature=0、top_p=1、输出上限 8192；Gemini 保留整张图集和详细提示。两者均记录用量。未开启额外思考覆盖参数。
- 修正版先复用 Qwen 基线首轮确定的车身锚点，再关联分批、补查、保护其他类别并逐路径复核车漆及装饰。它不读取 Gemini 的分类；同一协议在新稿件测试期间保持不变。
- 两个独立进程分别顺序处理 Qwen 和 Gemini；两种服务之间并行，同一服务内部不并发发起多个模型请求。共享网络或服务负载仍可能影响耗时，不以该测试做严格速度排名。
- `summary.json` 保存路径数、HTTP 次数、失败、耗时、待处理对象及与 Gemini 的交并差。Gemini 是对照，不是真值；`agreement_with_gemini_not_accuracy` 不可称为准确率，更不能自动用来宣布超过 Gemini。
- 可在各案例目录放 `source_visual_reference.json`，由离线汇总读取 `patterns`（每个图案单元的源路径集合，可用 `alternatives` 表示重叠副本等价选择）、`acceptable_source_ids` 和标注 `status`。推理阶段不读取该文件。未经用户确认的助手预览参考必须标明，不当作独立人工真值；“完整图案单元”也不等于视图套数。
- 修正版的 `requests`、`seconds` 为新增阶段；`bootstrap_requests` 及 `including_bootstrap_seconds` 将依赖的 Qwen 首轮计入。不能把复用首轮当成免费信息。
- `request_stages` 列出首轮、补查和局部复核的批次数；`bootstrap_http_attempts` 和 `bootstrap_failed_requests` 保留依赖首轮的实际尝试及失败，基线后续流程失败也不会抹去首轮成本。
- 三种结果的 `stripes.svg/png` 是彩条诊断预览；完整基线六类结果仍在 `components_report.json`。修正版目前仅输出实验彩条，不声称全六类已验收。
- 如果基线在补查或导出阶段失败，但完整首轮已有有效缓存，修正版可用其车身标签继续；基线仍标失败。没有有效 Qwen 锚点则修正版失败，禁止用 Gemini 锚点救场。
- 整车级混合组和每区域路径超限会写入 `unresolved`。不能隐藏这些限制，也不能仅因 JSON 合法或提取路径更多就认定质量更高。

完整运行目录应留在 `.local/tasks/`，可提交验收只保留脱敏摘要、小型预览及核查意见。源码与协议回归测试为 `tests/test_ai_extraction_benchmark.py`，不会调用真实服务。

2026-10-21—22 的三份新稿件实测见 [验收报告](../validation/qwen_tuning/holdout-comparison.md)，包含失败与退步结果；本次结果不支持将工程修正版直接设为默认。

离线汇总还生成 `comparison.html` 和逐稿 `comparison.png`，原稿与三种结果采用统一原稿坐标；可以点击网页中的图片放大核查遗漏与混入。
