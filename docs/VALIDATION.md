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

## 跨平台 Qt 前端（2026-08-20）

原生 Win32 GUI 保留为 Windows 默认前端；新增的 `asmr_gui` 是 PySide6 前端，任务、
播放器、下载、设置四页与原生版对应，同进程直接调用 `asmr_lrc`，不经过 JSONL worker。

### 已在本机（Windows 11）验证

- `ruff check .` 全通过；`pytest -q` 194 项全通过，其中新增 95 项：18 项跨平台目录解析、
  20 项凭据存储、19 项播放引擎、13 项原生/Qt 设置一致性、15 项 Linux 打包资产和 10 项
  文档相对链接检查。
- Qt GUI 真实启动：四页全部构造成功，导航为“任务/播放器/下载/设置”，窗口 1180×780，
  `platform: windows`、`style: fusion`、`exec returned 0`。`build_app_config` 把语境分析
  角色解析到 Qwen，而不是落到只做翻译的 TranslateGemma。
- `pip install .[gui]` 生成的 `asmr-translation.exe`（gui-script，走 pythonw 不闪控制台）
  以 `QT_QPA_PLATFORM=offscreen` 启动后持续运行，入口点与窗口构造均正常。
- conda 环境下 `import PySide6` 失败（`WinError 127`）的根因已定位：`Qt6Core.dll` 按无
  版本号的 `icuuc.dll` 链接系统 ICU，而 Anaconda 的 `Library\bin` 里有一份带版本号符号
  的 ICU 且排在 `System32` 之前。逐个扫描 `Qt6*.dll` 的导入表确认 `icuuc.dll` 是唯一可
  被这样顶替的非系统依赖，因此修复限定为在包导入时预加载 `System32\icuuc.dll`
  （`asmr_gui/qt_bootstrap.py`），没有改动全局 DLL 搜索策略，也不影响 CUDA 库发现。
- `python -m build --wheel` 成功：wheel 同时包含 `asmr_lrc`（26 个模块）与 `asmr_gui`
  （13 个模块），`gui_scripts` 里有 `asmr-translation = asmr_gui:main`，`COPYING` 作为
  `License-File` 入包，`Requires-Python` 为 `>=3.12,<3.14`，`keyring` 带
  `sys_platform != 'win32'` 标记（Windows 不会多装一份），分类器同时声明 Windows 与
  POSIX::Linux。因此 Linux 侧 `pip install .[gui]` 的元数据路径是实测可用的。
- 设置结构与 `native/src/settings.cpp` 的一致性由测试断言，而不是靠人工比对：22 个顶层
  键、5 个提供方键、原生文档往返不变、BOM 容错、协议推断、API Key 只随 OpenAI 提供方
  下发。文件位置也一并断言：Windows 侧两个前端都落在
  `%LOCALAPPDATA%\ASMR Translation\settings.json`，默认下载库都是 `<下载>\ASMR Translation`，
  改名会直接让测试失败而不是让用户拿到两份互不可见的配置。
- 写文档时在凭据存储里发现并修掉两个真实缺陷，都是“看起来成功”的静默失败：
  1. 清除 API Key 时，`CredDeleteW` 和 `keyring.delete_password` 的失败被无条件吞掉，
     用户以为密钥已删除，实际仍留在凭据管理器/钥匙环里。现在只有“条目本就不存在”
     （Windows 的 `ERROR_NOT_FOUND`，Linux 用删除后回读确认）才算成功，其余一律报错；
     无法确认是否删除时按失败处理。
  2. `backend_status()` 用类名里是否含 "fail" 判断 keyring 是否可用，而 keyring 的
     `backends.fail.Keyring` 和 `backends.null.Keyring` 类名都叫 `Keyring`，检测永远不会
     命中 —— 没有 Secret Service/KWallet 时反而会报告“后端可用”。改为按模块路径判断，
     并把 `null`（静默丢弃写入）一起拒绝；设置页显示的后端名也改成带模块的全名，
     因为几乎所有后端的类名都是 `Keyring`。
  Windows 侧用测试专属条目名 `ASMRTranslation/Test/<uuid>` 对真实凭据管理器做了写入、
  含日文的 UTF-16LE 回读、清除、重复删除的完整往返，不会碰到四个真实角色条目。
- `asmr_lrc/platform_paths.py` 的 Linux 分支虽然没有实机，但已被单测钉住：XDG 三个变量
  生效、缺失时回退到 `.config`/`.cache`/`.local/state`、相对路径值按规范忽略、
  `user-dirs.dirs` 里本地化的 `XDG_DOWNLOAD_DIR="$HOME/下载"` 能被解析且不会把
  `XDG_DESKTOP_DIR` 当成下载目录、缓存键的大小写折叠只在 Windows/macOS 打开。
- Linux 打包资产由测试守住：desktop 条目必填键、`Exec=@LAUNCHER@` 占位符、
  `StartupWMClass` 与 `setDesktopFileName()` 一致、LF 行尾、脚本不调用 sudo、除 pip 外
  不下载任何东西、Python 版本闸门与 `requires-python` 一致、`install.sh` 以 100755 入库。
  `bash -n` 语法检查通过。

### 尚未验证（本会话没有 Linux 机器）

以下都是纸面正确、未经实机确认，不能当成发布门槛通过：

- 在任何发行版上真正跑过 `packaging/linux/install.sh`。README 里的包名来自各发行版文档，
  不是实测安装结果。
- Qt 在 X11/Wayland 下的实际启动、`libxcb-cursor0` 缺失时的报错文案、PortAudio 输出、
  fcitx5/ibus 中文输入法在真实会话中的表现。
- Linux 上的 CUDA：`dlopen` 顺序、`LD_LIBRARY_PATH` 是否被 ASR 子进程正确继承。
- `keyring` 与 Secret Service / KWallet 的实际读写往返。
- desktop 条目在真实菜单中的显示，以及图标在各主题尺寸下的渲染。
- Qt GUI 的播放器实机验收（拖动、倍速、点击台词跳转、双击编辑），与下面“仍需人工验收”
  的项目同级别。

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
- Python 3.12.7 项目 `.venv`；PySide6 6.11.2、sounddevice 0.5.6、numpy 2.5.1。
- `faster-whisper-large-v3`，CUDA，`int8_float16`。
- Ollama `qwen3.5-9b-abliterated:latest`。
- FFmpeg 可执行文件来自本机 PATH。
- 没有 Linux 实机；跨平台部分的实机验收见上面的“尚未验证”。

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
