# Windows 故障排查

## 按钮没有反应或任务立即结束

先打开“任务 → 环境探测”。如果 Python 路径显示为 `python` 或文件不存在，打开
“设置 → 依赖向导”完成嵌入式 Python/ASR 安装；开发环境则填写
`.venv\Scripts\python.exe`。日志和设置位于 `%LocalAppData%\ASMR Translation`。

如果 worker 启动后立即退出，确认路径指向 `python.exe` 而不是目录，并从设置页保存一次。
安装版不要把 Python 路径改成 MSI 安装目录中的不存在文件。

## 没有 GPU 或 CUDA 错误

- 在 NVIDIA 驱动正常且 `nvidia-smi` 可用后，重新运行环境探测。
- 向导选择 CUDA 时必须使用兼容的 NVIDIA 驱动；否则选择 CPU 依赖完成诊断。
- 显存不足时降低 ASR 模型或改用 CPU；程序不会静默切换模型。
- `model.bin` 是二进制文件。浏览器若保存为 `.mht`，确认文件头和来源后只改扩展名，
  不要转换内容。

## Ollama 或翻译失败

确认 Ollama 服务正在运行，设置中的 Base URL（默认 `http://127.0.0.1:11434`）可访问，
并且模型标签完全匹配。依赖探测只会报告缺失模型和精确安装命令，不会静默下载；
手工执行 `ollama pull translategemma:4b` 和（如缺失）
`ollama pull qwen3.5-9b-abliterated:latest`。外部 OpenAI-compatible API 的 Key
只保存在 Credential Manager；401、429、超时和格式错误会写入日志，不会静默回退到别的模型。

默认角色是 TranslateGemma 主翻译、Qwen 语境分析/失败兜底；TranslateGemma 使用
`translategemma` 协议（空 system、`temperature=0`、严格 JSON），并保留成人、粗俗和
耳语表达。质量模式会先完成全部 Qwen 语境，再卸载 Qwen 才进入主翻译。GTX 1660 Ti
6GB 不应同时运行 Whisper、Qwen 和 TranslateGemma；遇到 OOM，先确认 ASR 已结束，
再把批量大小降到 6 或使用平衡模式。

## FFmpeg 或播放器打不开音频

在设置中填写 `ffmpeg.exe` 的完整路径，或把它加入 PATH 后重新探测。系统不支持的
FLAC/Opus/Ogg 会生成不修改原文件的 PCM WAV 播放代理；代理位于缓存目录，残缺临时文件
可以安全删除。

## 下载失败、镜像和断点续传

- 确认 endpoint、代理和连接超时；不要填写带临时签名参数的 URL 到日志或设置。
- 401/403/404 或错误 manifest 会立即失败；429、5xx、断流和超时使用有限重试。
- `.part` 文件会按大小继续下载。哈希不匹配时临时文件会删除，不覆盖已有完整文件。
- 镜像必须由用户明确填写；程序不会在失败后自动尝试未知镜像。
- 取消下载后等待任务退出，再次启动即可恢复；必要时删除对应 `.part` 后重新开始。

## 外部 API 的数据范围

外部审校只接收转写文本、目标行、上下文和术语，不接收音频文件。首次使用前会显示
预计字符量；取消授权即可保持本地 Ollama 流程。

## 收集诊断信息

提交问题时请附上：Windows 版本、GPU/驱动、环境探测结果、相关日志和复现步骤。请先
删除 API Key、Cookie、签名下载 URL 及个人路径；不要上传原始音频或整份 Credential。
