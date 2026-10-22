# 源码体验包与一键构建验收

日期：2026-10-22。基于 `10.23客户演示分支` 当前工作区（包含并发任务已有修改），未自动提交。

- 从 `codex/baseline-1c6162f-colors-dual-ai` 移入源码包复用接口、真实 Python 3.10 语法检查及相关测试；联合构建移入同时输出源码 ZIP 的流程。
- 新增 `scripts/package_source.cmd`，双击仅生成同事源码体验 ZIP。现有 `scripts/build_windows.cmd` 同时生成 EXE ZIP 和源码 ZIP。
- 修正 `ui.py` 一处嵌套引号，使同一份源码可由 Python 3.10 编译。

验证结果：

- `python -X utf8 -m unittest tests.test_source_package tests.test_source_runtime -v`：12 项通过，无收费接口调用。
- `python -X utf8 app.py --self-test`：当前 Python 3.13 环境通过。
- `python -X utf8 scripts/package_source.py`：独立源码 ZIP 生成、逐文件校验通过。
- Python 3.10.18 临时隔离环境安装 `requirements.txt` 全部二进制依赖成功；运行实际解压包 `app.py --self-test` 通过，依赖版本与导入检查通过。未更改本机共享依赖。
- `python -X utf8 scripts/build_windows.py`：完整构建成功，源码自检、EXE 自检、继承配置后自检及 ZIP 完整性检查通过。供分享 ZIP 不含本机配置或密钥。
- 最终源码 ZIP 共 32 个文件，SHA-256 清单及当前源码逐文件比对通过。`git diff --check` 通过。

交付位于 `release/`，文件名后缀为 `20261022_152749_196dc4`，包括源码体验 ZIP、EXE ZIP 和可运行 EXE 目录。旧发布包保留。

兼容范围为 Windows 标准 CPython 3.10～3.14 x86-64；真实低版本运行验收覆盖 3.10.18，其余版本未逐一实机验证。不支持 3.9 及以下。未执行真实 FS 的 AI 识别验收或全量回归测试。

临时 Python 3.10 环境与解压目录位于 `.local/tasks/20261022_source_packaging/`；清理命令被自动安全策略拒绝，因此保留，未删除文件。构建日志保留在 `.local/tasks/20261022_packaging/`，发布包及原有用户文件保留。
