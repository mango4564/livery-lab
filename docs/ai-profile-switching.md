# AI 配置快捷切换

## 双窗口对比

双击 `scripts/双开AI对比.cmd`，同时打开当前源码的两个独立窗口，标题分别为“全车矢量提取工作台 · Gemini”和“全车矢量提取工作台 · 社内”。使用本机已有 Python 及依赖，无需打包。分别添加同一个 FS 文件，保持相同提取选项，再点击开始即可对比；启动脚本本身不请求接口。

两套模型从下述 `.local/ai-profiles.json` 读取。每次双开复制当前源码 `settings.json` 的超时等选项，覆盖对应模型配置并启用 AI。窗口配置分别保存在 `.local/tasks/日期_ai_comparison/本次编号/gemini或internal/settings.json`，启动日志也在对应目录；父进程的 `LIVERY_*` 环境变量不会覆盖对比配置。可在各自 AI 配置窗口内调整参数，原有源码和 EXE 的设置不会被覆盖，后续快捷切换也不会改变已打开的对比窗口。

默认输出分别为 `output/ai-comparison/本次编号/gemini/` 和 `output/ai-comparison/本次编号/internal/`，每次双开使用新目录，避免复用旧结果。为保持对比独立，请保留各自输出路径。关闭一个窗口不影响另一个窗口。只检查本地配置可执行 `python -X utf8 scripts/compare_ai.py --dry-run`。

## 单窗口切换

双击 `scripts/切换到社内AI.cmd` 或 `scripts/切换到Gemini.cmd` 即可切换。也可双击 `scripts/switch_ai.cmd` 后按 1 或 2。脚本使用本机已有 Python，无需重新打包 EXE，不会测试接口或调用模型。

默认同步源码目录、最新构建 EXE 目录，以及当前正在运行且位于本仓库 `release/` 内的其他 EXE 目录。控制台显示实际目标目录。请在当前提取完成后再切换，下次开始提取会读取新配置；界面状态可通过重新打开 AI 配置或重启刷新。

只替换地址、模型、密钥并启用 AI；本地模型配置可指定系统代理选项。保留超时、输出等其他设置。切换前备份至 `.local/backups/ai_switch_时间_编号/`；同配置不重复写入。先校验全部目标，再逐文件原子写入；写入失败时回滚已经修改的文件。备份 `manifest.json` 记录每份原设置的对应位置。

私有配置是 `.local/ai-profiles.json`，该路径被 Git 忽略，不随脚本提交或打入分享 ZIP。首次在新电脑上使用时，按下列结构自行填入两套地址和密钥：

```json
{
  "internal": {"base_url": "", "model": "", "api_key": "", "use_system_proxy": false},
  "gemini": {"base_url": "", "model": "", "api_key": "", "use_system_proxy": false}
}
```

预览和指定目标（源码路径按实际位置替换）：

```powershell
python -X utf8 scripts/switch_ai.py gemini --dry-run
python -X utf8 scripts/switch_ai.py internal --app-dir "release/某版本/彩条提取工作台"
```

`--app-dir` 只修改指定目录，可重复指定。脚本不重启、不关闭正在运行的程序，也不修改已有提取结果。已有 `LIVERY_BASE_URL`、`LIVERY_MODEL` 等环境变量仍按程序原有优先级覆盖本机文件；使用快捷切换时应移除对应环境变量。
