# AI 请求对比

批次是模型逻辑请求；HTTP 次数包含传输重试。未返回的 token 用量标为未知。

## 任务 1

逻辑请求 20 批；HTTP 20 次；重试 0 次；失败 0 批；累计 315.784 秒。

| 模型 | 阶段/批次 | 候选数 | 图片尺寸 | HTTP | 秒 | 输入/输出 token | 结果 |
|---|---|---:|---|---:|---:|---|---|
| qwen3.8-27b | classification/01 | 12 | 1800×953 + 800×1140 | 1 | 21.453 | 5078/406 | accepted |
| qwen3.8-27b | classification/02 | 12 | 1800×953 + 800×1140 | 1 | 24.126 | 5102/478 | accepted |
| qwen3.8-27b | classification/03 | 12 | 1800×953 + 800×1140 | 1 | 17.154 | 5025/321 | accepted |
| qwen3.8-27b | classification/04 | 12 | 1800×953 + 800×1140 | 1 | 21.233 | 5004/408 | accepted |
| qwen3.8-27b | classification/05 | 12 | 1800×953 + 800×1140 | 1 | 13.206 | 5010/232 | accepted |
| qwen3.8-27b | classification/06 | 12 | 1800×953 + 800×1140 | 1 | 23.095 | 5002/453 | accepted |
| qwen3.8-27b | classification/07 | 12 | 1800×953 + 800×1140 | 1 | 17.873 | 5011/307 | accepted |
| qwen3.8-27b | classification/08 | 12 | 1800×953 + 800×1140 | 1 | 11.616 | 5021/195 | accepted |
| qwen3.8-27b | classification/09 | 12 | 1800×953 + 800×1140 | 1 | 13.933 | 5036/207 | accepted |
| qwen3.8-27b | classification/10 | 12 | 1800×953 + 800×1140 | 1 | 18.789 | 5011/330 | accepted |
| qwen3.8-27b | body_surface_audit/01 | 12 | 1800×954 + 800×1140 + 800×950 | 1 | 12.609 | 5511/224 | accepted |
| qwen3.8-27b | body_surface_audit/02 | 12 | 1800×954 + 800×1140 + 800×950 | 1 | 15.164 | 5515/261 | accepted |
| qwen3.8-27b | body_surface_audit/03 | 12 | 1800×954 + 800×1140 + 800×950 | 1 | 12.279 | 5520/199 | accepted |
| qwen3.8-27b | body_surface_audit/04 | 12 | 1800×954 + 800×1140 + 800×760 | 1 | 14.894 | 5373/255 | accepted |
| qwen3.8-27b | body_surface_audit/05 | 12 | 1800×954 + 800×1140 + 800×760 | 1 | 12.799 | 5368/221 | accepted |
| qwen3.8-27b | body_surface_audit/06 | 12 | 1800×954 + 800×1140 + 800×760 | 1 | 15.023 | 5374/255 | accepted |
| qwen3.8-27b | body_surface_audit/07 | 12 | 1800×954 + 800×1140 + 800×760 | 1 | 11.238 | 5365/177 | accepted |
| qwen3.8-27b | body_surface_audit/08 | 12 | 1800×954 + 800×1140 + 800×760 | 1 | 15.509 | 5370/243 | accepted |
| qwen3.8-27b | body_surface_audit/09 | 12 | 1800×954 + 800×1140 + 800×760 | 1 | 12.898 | 5373/219 | accepted |
| qwen3.8-27b | body_surface_audit/10 | 12 | 1800×954 + 800×1140 + 800×570 | 1 | 10.893 | 5219/175 | accepted |

实际发送参数（未列出的采样/思考参数沿用服务端默认；接收参数不等于服务端确实执行）：

20 批：
```json
{
  "max_tokens": 8192,
  "model": "qwen3.8-27b",
  "response_format": {
    "type": "json_object"
  },
  "stream": true,
  "stream_options": {
    "include_usage": true
  }
}
```

## 任务 2

逻辑请求 2 批；HTTP 2 次；重试 0 次；失败 0 批；累计 340.455 秒。

| 模型 | 阶段/批次 | 候选数 | 图片尺寸 | HTTP | 秒 | 输入/输出 token | 结果 |
|---|---|---:|---|---:|---:|---|---|
| gemini-3.8-flash-high | initial/gemini_control | 120 | 1800×953 + 1600×5700 + 1600×3800 | 1 | 107.891 | 20904/2034 | accepted |
| gemini-3.8-flash-high | body_surface_audit/body_surface_audit | 120 | 1800×954 + 1600×5700 + 1800×563 | 1 | 232.564 | 19554/469 | accepted |

实际发送参数（未列出的采样/思考参数沿用服务端默认；接收参数不等于服务端确实执行）：

2 批：
```json
{
  "model": "gemini-3.8-flash-high",
  "response_format": {
    "type": "json_object"
  },
  "stream": true,
  "stream_options": {
    "include_usage": true
  }
}
```

