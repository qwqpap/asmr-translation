# v0.3 验证记录

日期：2026-08-10
版本：0.3.0

## TranslateGemma 4B 接入记录（2026-08-16）

- 默认角色已落地：`translategemma:4b` 主翻译，
  `qwen3.5-9b-abliterated:latest` 语境分析和失败兜底；全量审校默认关闭。
- `chat-json` 与 `translategemma` 协议都纳入提供方和缓存 profile；旧配置缺少协议时按
  模型名兼容推断，旧 Qwen 配置继续保持单模型行为。
- 质量流水线已拆成全局阶段：全部 ASR → 全部上下文 → 卸载分析模型 → 全部批量翻译；
  主翻译失败时先卸载 TranslateGemma，再加载 Qwen，成功后卸载兜底模型。
- 依赖探测不会拉取模型；缺失模型会返回精确的 `ollama pull <model>` 命令。模型仍放在
  当前 C 盘 Ollama 目录，TranslateGemma 约 3.3GB，现有 Qwen 约 5.3GB。
- 仓库测试包含 20 条明确成人语境合成日语样例，覆盖直白/委婉表达、同意/拒绝、否定、
  体位指令和耳语；这些是结构与提示回归 fixture，不含真实用户字幕。

### TranslateGemma 实机结果

- `tools/validate_translation_model.py --rounds 30`：30/30 批严格 JSON 通过，均为
  `attempts=1`，没有拆批、缺失 ID 或重复 ID。
- 30 项质量 fixture：总计 25/30；可翻译项 21/24（87.5%），严重项 0 失败。
  未通过的可翻译项为 `s028`、`s042`、`s060`，ASR 根因项为 `s036`、`s044`；因此计划
  要求的 24/24 尚未达到，不能把这次结果写成质量门槛通过。
- 20 条成人合成样例分成 2 个批次，JSON/ID 全部通过；模型没有输出拒译文本，否定、
  同意/拒绝、体位和耳语语义均保留。一次外部宽泛关键词统计把含“被拒绝”的 `adult-18`
  误报为拒译，代码中的“拒绝翻译/无法翻译”检测不会将该正常译句拦截。
- 当前机器 `ollama list`：TranslateGemma 约 3.3GB、Qwen 约 5.3GB，Ollama 模型总空间
  约 8.54GiB，仍在 C 盘；C 盘当时剩余约 38.16GiB。TranslateGemma 12 行批次冷启动约
  6.86 token/s，warm 约 50.86 token/s，峰值约 5550/6144MiB、100% GPU；Qwen 最近 4
  行 warm 约 11.8 token/s（约 5.8GB，29% CPU/71% GPU）。实测未出现 OOM。
- 阶段化单测和质量工具均验证“全部上下文后卸载 Qwen，再翻译”；主翻译失败的合成单测
  验证了先卸载 TranslateGemma、再加载并卸载 Qwen 的顺序。真实 30 条差译盲测和
  100%/150%/200% DPI 全套人工截图仍未完成。

质量 24/24、盲测 24/30 优于当前模型、完整 DPI/听感验收等门槛仍未全部满足；当前安装
 profile 已保存为 TranslateGemma 主翻译、Qwen 分析/兜底，Qwen 旧方案仍保留为兼容/可选
 路径，不把未完成门槛宣称为全面替换。

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
