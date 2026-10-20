# 视觉接口与并发验收摘要

以下为 2026-10-19 未提交工作区的历史联调结论，清理时从本机报告提炼。
完整运行目录、原始响应及包含本机地址的配置备份不提交。

- 流式 Chat Completions：完整接收 `finish_reason=stop` 和 `[DONE]` 后才接受 JSON；当时 89 项离线检查通过。
- 代理设置与直连排查：当时 93 项离线检查通过；服务端地址与账户信息不保留在仓库。
- 并发回归：10 份真实 FS，峰值 10 个同时任务，10 份完成、0 失败，源文件摘要不变；当时 95 项离线检查通过。
- 早期接口打包验证中，启动自检通过，但 AI 工作进程退出码为 2；这是旧版本失败记录，不作为当前版本通过依据。

源码回归见根目录的 `test_batch_concurrency.py`、`test_vision_stream.py`、
`test_vision_retry.py`、`test_vision_proxy.py`。默认测试不请求真实付费接口。
当前独立曲线识别验收见 `validation/independent_stripes_20261019/`。

## 2026-10-20 本地 Qwen 服务故障排查

- 输入：用户提供的十文件批处理日志。首轮五个成功、五个超时；失败任务重试后两个成功、三个仍超时。这里的成功仅代表产生待复核候选。
- 实测：使用用户指定的同一网关、模型和密钥，直连发送最小文字请求。两次普通流式请求、一次带 `chat_template_kwargs.enable_thinking=false` 的流式请求均在约 5 秒返回 HTTP 503。之后调用程序实际的非流式 `test_connection`，同样返回 503。
- 脱敏错误摘要：LiteLLM 模型组上游 `upstream connect error or disconnect/reset before headers`，`delayed connect error: 111`，网关未执行 fallback。未保留密钥或完整真实响应。
- 结论：排查时网关可达，但网关连接模型上游失败；最小文字请求已失败，无需上传真实大图即可确认当前服务故障。必须由服务人员检查。当前证据不能证明历史批量超时与本次上游连接失败完全同因，也不能证明十路并发可稳定承载。
- 请服务方检查：模型进程及监听端口；网关中该模型组的上游地址、端口与健康状态；从网关容器或主机访问上游的连通性；相关容器网络、防火墙和服务重启日志。恢复后，先验收单文件真实图片，再逐步升至 2、5、10 路，检查排队、推理耗时和网关超时。
- 客户端改动：对 502、503、504 显示固定的服务端排查提示；连接测试关闭 HTTP 错误响应。保留原有重试次数、超时和严格结果验证，不自动切换模型。
- 离线验证：`python -X utf8 -m unittest test_vision_retry test_vision_stream test_vision_connection test_vision_proxy tests.test_vision_service_errors -v`，22 项通过。新增 `tests/test_vision_service_errors.py` 检查服务故障提示、重试、资源关闭和响应脱敏；默认不发真实请求。
- 发布验证：`python -X utf8 scripts/build_windows.py --label 接口故障诊断版` 成功；源码自检、EXE 自检、继承本机配置后的 EXE 自检和 ZIP 完整性及配置排除检查通过。新版保存在 `release/接口故障诊断版_20261020_103439_787854/`，旧包保留；临时联调脚本已删除，构建日志保留在 `.local/tasks/`。
- 限制：服务未恢复，未完成真实图片及十文件批量复测，不宣称识别故障已修复。排查没有修改用户现有 AI 配置或删除既有成果。
