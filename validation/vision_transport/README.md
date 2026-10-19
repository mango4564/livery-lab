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
