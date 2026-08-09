# v0.3 验证记录

日期：2026-08-10
版本：0.3.0

## 已完成证据

- Python 自动测试覆盖上下文窗口、动态 Schema、术语证据、双阶段路由、缓存失效、
  有反馈重试、批次二分恢复、单 ID 术语修复、取消、JSONL worker 和编辑备份。
- 本地假 OpenAI-compatible 服务器覆盖严格 Schema 降级、401、429、超时、畸形 JSON，
  并验证 Key 不进入设置、缓存、日志、profile 或子进程命令行。
- `context-v4` 质量 fixture：30 项总计 28 项命中；24/24 个可翻译项通过，比例 100%；
  “松果、滑梯、《少爷》、帮工”四个严重案例全部通过。剩余两项为固化的 ASR 根因。
- 真实 983.67 秒 MP3 复用 Whisper 缓存完成过 254 条全量初译和审校；结构为 0 缺失、
  0 额外、0 重复 ID，LRC 为 254 行。后续 v4 长任务按用户要求停止，partial 可恢复。
- Win32 Release 构建成功；原生测试覆盖 UTF、设置、JSONL、LRC、活动行选择和
  Media Foundation 消息循环。
- Media Foundation 已用生成的 Unicode 路径 PCM WAV、真实 MP3 和 FFmpeg PCM 代理 WAV
  验证。此前无法打开音频的根因是 `UrlCreateFromPathW` 空缓冲探测，现已修复。
- FFmpeg 为真实 MP3 生成 188,864,954 字节代理，并验证缓存复用和残缺临时文件清理。
- GUI worker 已验证在父进程保持 stdin 管道打开时正常退出，不再触发 Python
  `_enter_buffered_busy` fatal error。
- 125% DPI 的真实窗口曾完成任务/播放器/设置页截图检查；复选框截字与字体问题已修复。

## 实机环境

- Windows 11，NVIDIA GeForce GTX 1660 Ti 6 GiB。
- Python 3.12 项目 `.venv`。
- `faster-whisper-large-v3`，CUDA，`int8_float16`。
- Ollama `qwen3.5-9b-abliterated:latest`。
- FFmpeg 可执行文件来自本机 PATH。

## 仍需人工验收

- 100% / 150% / 200% DPI 的真实窗口逐页检查。
- 用真实窗口完成播放、拖动、倍速、音量、点击台词跳转、双击编辑和 `.lrc.bak` 试听验收。
- 极轻耳语、普通轻声、敲击、长静音、长音频五类样本的逐段人工听感复核。
- 外部 API 真实供应商差异与用户自己的数据授权确认。

本轮 Computer Use 在窗口状态捕获时连续返回 `node_repl exec context not found`，因此没有
用自动注入冒充最终 GUI 人工验收。原生媒体消息循环和缓存/worker 自动测试是补充证据，
不替代上述视觉与听感门槛。

## MSI 骨架验证

- WiX v4 工程、当前用户目录、开始菜单快捷方式和升级代码已加入 `installer/`。
- GUI 已支持安装版嵌入式 Python 路径，并在检测到安装器引导资源且没有可用 Python 时
  显示依赖向导。
- `bootstrap.ps1` 已通过 Windows PowerShell 5.1 语法检查，具备 `.part`、Range 续传、
  SHA-256、压缩包目录穿越保护和 JSONL 事件输出。
- 当前工作机没有 WiX，且 manifest/CPU/CUDA lock 仍是待发布版本的占位哈希，因此尚未
  生成可发布 MSI；这不是通过自动测试伪造的绿灯。填充固定依赖哈希后运行
  `docs/BUILD_INSTALLER.md` 的命令完成发布前构建和普通用户实窗验收。
