# ASMR Translation v0.3

Windows 与 Linux 本地日语 ASMR 转写、上下文翻译与同步台词播放器。

源码仓库：[github.com/qwqpap/asmr-translation](https://github.com/qwqpap/asmr-translation)。

- Python 核心使用 `faster-whisper` 转写，默认通过本机 Ollama 翻译。
- 两个前端共用同一套任务、播放器、下载、设置四页和同一份 `settings.json`：Windows 的
  C++20 原生 Win32 GUI（不依赖 Qt/WPF），以及 Windows/Linux 通用的 Qt 6 GUI
  （PySide6，同进程直接调用 `asmr_lrc`，不经过 worker 进程）。
- 默认由 `translategemma:4b` 专职完成日语→简体中文主翻译；当前 Qwen
  `qwen3.5-9b-abliterated:latest` 只做语境分析和 TranslateGemma 失败兜底。
- 质量模式按全局阶段执行：全部 ASR → 全部 Qwen 语境分析并卸载 → 12 行批量
  TranslateGemma；全量二次审校默认关闭，可在设置页单独启用。
- 可分别为初译和终审选择 Ollama 或 OpenAI-compatible `/v1/chat/completions`。
- 音频默认不上传；外部 API 只发送用户明确授权的转写文本。

## 主要能力

- 前后各 8 行只读上下文、人物/风格/话题记忆和带证据 ID 的术语表。
- 精确 ID 动态 JSON Schema，校验缺失、额外、重复、合并、空译文和日文残留。
- 有反馈的有限重试、失败批次二分恢复和单 ID 术语修复。
- draft/review 分阶段缓存与 `review_changed`、`asr_suspect`、`term_conflict`、
  `term_repaired`、`low_confidence` 标志。
- UTF-8 JSONL GUI worker：`probe`、`run`、`load_cues`、`save_edits`、
  `prepare_playback`、`download_plan`、`download_run`（原生 GUI 与脚本使用）。
- 播放、跳转、音量、0.75–2.0 倍速、播放列表和 50 ms 台词刷新；原生 GUI 用 Media
  Foundation，Qt GUI 自行解码 PCM 并经 PortAudio 输出，倍速用 WSOLA 保持音高。
- 中文主行、日文副行、点击跳转、双击编辑；首次编辑备份 `.lrc.bak`，之后原子保存。
- 系统不支持的格式可由 FFmpeg 生成 PCM WAV 代理，默认 4 GiB LRU 缓存。
- 协作取消后由 Win32 Job Object（Linux 为进程组 `killpg`）兜底清理 Python/ASR/curl
  子进程树。

## RJ 一站式下载

下载页接受 `RJ01528633`、数字 RJ 编号或 DLsite 作品链接。默认资料库为
`%USERPROFILE%\\Downloads\\ASMR Translation`（Linux 为 xdg-user-dirs 的下载目录下的
`ASMR Translation`），默认只选择时长和文件名可靠匹配的较小音频版本；
可以改为全部音频、全部文件或手动勾选。文件使用 `.part` 和 HTTP Range 续传，完成后校验大小并原子改名，
每个作品保存 `download.manifest.json`。endpoint、curl、代理和连接超时均可在设置页修改，不会静默切换镜像。

下载功能改编自 [thiliapr/asmr-one-downloader](https://github.com/thiliapr/asmr-one-downloader)，
保留其作者署名、AGPL-3.0-or-later 许可和源码链接，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
请只下载你有权访问的内容；本项目不处理账号、Cookie、付费登录或访问限制绕过。

## 安全边界

- 不修改、重编码或删除原音频。
- 默认不覆盖已有 LRC；CLI 只有 `--overwrite` 才覆盖。
- Whisper 转写缓存与翻译 v2 缓存分离，升级提示词不会重跑昂贵 ASR。
- GUI 的 API Key 存入 Windows Credential Manager（Linux 经 `keyring` 存入桌面钥匙环），
  不进入设置、缓存、日志或命令行；没有可用后端时明确报错，不退化成明文保存。
- CLI 的外部 Key 只从指定环境变量读取；非交互外发还需 `--allow-external-text`。
- 不静默切换模型；开发环境不自动下载依赖，MSI 只有在用户勾选并确认后才运行引导下载。

## 环境与安装

### 普通用户：轻量 MSI

从 GitHub Releases 下载 `asmr-translation-0.3.0-x64.msi`，双击即可按当前用户安装，
不需要管理员权限。安装包只包含 GUI、项目 wheel、依赖清单和引导程序，不捆绑 Python、
CUDA、FFmpeg、Ollama 或 Whisper 模型。首次启动会打开依赖向导；只有勾选项目并点击
“安装所选”后才会联网，Whisper 模型默认不勾选。

详细步骤、镜像规则、隐私边界和卸载行为见 [docs/INSTALL_WINDOWS.md](docs/INSTALL_WINDOWS.md)，
常见问题见 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)。

MSI 只安装原生 Win32 GUI。想在 Windows 上用 Qt GUI，请按下面的手工环境安装
`.[gui]`；两者读写同一份 `settings.json`，可以随时换着用。

### Linux 用户：用户级安装脚本

```bash
git clone https://github.com/qwqpap/asmr-translation
cd asmr-translation
./packaging/linux/install.sh          # 需要 CUDA 时加 --cuda
```

脚本只写入家目录：在 `~/.local/share/asmr-translation/venv` 建 venv、安装 `.[gui]`、
装好 desktop 条目和图标，并检查 ffmpeg、PortAudio 与 Qt xcb 依赖。它不使用 sudo，
不安装发行版软件包，也不下载任何模型。各发行版包名、输入法（fcitx5/ibus）配置、
Wayland 回退和卸载方式见 [packaging/linux/README.md](packaging/linux/README.md)。

### 开发者：手工环境

要求 Windows 10/11 或较新的 Linux 桌面、Python 3.12/3.13、FFmpeg；本地翻译还需要
Ollama。CUDA 为当前 `large-v3` 实机配置，CPU 仅适合诊断。TranslateGemma 与 Qwen
模型都保留在当前 Ollama 目录，不会由程序静默下载。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,gui,cuda]"
.\.venv\Scripts\python.exe -m asmr_lrc --probe
# 依赖向导/探测报告缺失模型时，按报告中的精确命令手工安装：
ollama pull translategemma:4b
ollama pull qwen3.5-9b-abliterated:latest
```

Linux 上的等价命令：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev,gui,cuda]"
.venv/bin/python -m asmr_lrc --probe
```

可选依赖：`gui` 是 Qt 前端（PySide6-Essentials、sounddevice，非 Windows 另加
keyring），`cuda` 是 pip 版 CUDA 运行库，`dev` 是测试与 lint 工具。

GTX 1660 Ti 6GB 的默认规则是阶段化加载：Whisper ASR 子进程结束后才加载
Qwen；全部语境完成后卸载 Qwen，再加载 TranslateGemma。失败批次才再次加载
Qwen，两个翻译模型不会默认同时常驻；预计需要约 3.3GB TranslateGemma 磁盘空间
加上现有 Qwen 约 5.3GB（另有 Ollama 元数据和缓存余量）。

项目不会自动下载模型。准备好的 faster-whisper 目录应包含 `model.bin`；浏览器有时会把
二进制权重误存为 `.mht`，确认文件头与目标后只需改名，不要转换内容。
两个 GUI 都会自动使用项目下的 `models/faster-whisper-large-v3`，也可在“设置 → ASR
模型目录”中改为其他完整的 faster-whisper 模型目录。

## 启动 Qt GUI

安装 `gui` 可选依赖后有三种等价入口：

```bash
asmr-translation                 # gui-script，Windows 下走 pythonw.exe 不闪控制台
python -m asmr_gui
python -c "import asmr_gui; raise SystemExit(asmr_gui.main())"
```

务必从 `asmr_gui` 进入，不要自己先 `import PySide6`：包导入时会先把 `icuuc.dll` 钉在
系统副本上，否则 conda 环境下 Qt 会加载错误的 ICU 并以 `WinError 127` 失败，详见
[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)。

## 构建与启动原生 GUI（Windows）

使用 Visual Studio 2022 的 x64 C++ 工具链：

```powershell
cmake -S native -B native/build -G "Visual Studio 17 2022" -A x64
cmake --build native/build --config Release
.\native\build\Release\asmr-translation.exe
```

也可把音频路径作为 EXE 参数，或拖放到窗口。开发环境 GUI 会向上寻找项目 `.venv`；
MSI 安装版优先使用 `%LocalAppData%\ASMR Translation\runtime` 下的嵌入式 Python，
并从 PATH 查找 FFmpeg。所有路径都可在设置页修正，设置和缓存不会写入安装目录。

## CLI 用法

```powershell
# 计划与环境
.\.venv\Scripts\python.exe -m asmr_lrc "D:\ASMR" --dry-run
.\.venv\Scripts\python.exe -m asmr_lrc --probe

# 默认质量模式
.\.venv\Scripts\python.exe -m asmr_lrc "D:\ASMR" --quality-mode quality

# 显式指定角色（默认已经是这组）
.\.venv\Scripts\python.exe -m asmr_lrc "D:\ASMR" `
  --ollama-model translategemma:4b --ollama-protocol translategemma `
  --analysis-model qwen3.5-9b-abliterated:latest `
  --fallback-model qwen3.5-9b-abliterated:latest --no-review

# 单遍平衡模式
.\.venv\Scripts\python.exe -m asmr_lrc "D:\ASMR" --quality-mode balanced --no-review

# 只复用已有转写重新翻译
.\.venv\Scripts\python.exe -m asmr_lrc "D:\ASMR" --translate-only --overwrite
```

外部 OpenAI-compatible 示例：

```powershell
$env:ASMR_TRANSLATION_API_KEY = "<key>"
.\.venv\Scripts\python.exe -m asmr_lrc "D:\ASMR" `
  --draft-provider ollama `
  --review-provider openai `
  --review-base-url "https://example.com/v1" `
  --review-model "review-model" `
  --openai-api-key-env ASMR_TRANSLATION_API_KEY `
  --allow-external-text
```

固定术语文件为 UTF-8 JSON：

```json
{
  "schema_version": 1,
  "terms": [
    {"source": "松ぼっくり", "target": "松果"},
    {"source": "坊っちゃん", "target": "《少爷》"}
  ]
}
```

通过 `--glossary glossary.json` 使用。用户固定术语可跨同一资料库复用；模型自动提取的
术语只保存在当前音频的语境缓存中。

## 缓存

默认位于 `.cache/<path-hash>-<content-hash>/`：

```text
source.json
transcript.raw.json
transcript.filtered.json
translation.context.json
translation.zh-CN.draft.json
translation.zh-CN.review.partial.json
translation.zh-CN.json
process.log
```

翻译 v2 profile 包含提供方、模型、协议、主翻译/分析/兜底角色、提示词版本、上下文
策略、审校模式和术语哈希，不含 API Key；切换 Qwen 与 TranslateGemma 不会误复用旧
缓存。损坏缓存会隔离为 `*.corrupt-N`，旧 Schema 会保留为 `*.stale-N`。

## 验证

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
cmake --build native/build --config Release
ctest --test-dir native/build -C Release --output-on-failure
.\.venv\Scripts\python.exe tools\evaluate_quality_cases.py
```

Linux 上跑前两条和最后一条即可（`native/` 只在 Windows 构建）：

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q
.venv/bin/python tools/evaluate_quality_cases.py
```

Python 测试共 194 条，覆盖 XDG/LOCALAPPDATA 目录解析、凭据存储、进程树清理、播放引擎、
原生/Qt 设置的结构与文件位置一致性、Linux 打包资产和文档相对链接；不需要显卡、Ollama
或音频设备（凭据往返测试只在 Windows 上跑，且使用测试专属条目名）。

基线 Qwen 质量 fixture 的可翻译项为 24/24，四个严重案例全部通过；当前 TranslateGemma
实机结果为 21/24 个可翻译项通过、严重项零失败，尚未满足 24/24 发布门槛。仓库另有 20
条明确成人语境合成 fixture，检查拒译、否定反转、体位指令和耳语表达不会被净化。结构
通过仍不代替 TranslateGemma 实机盲测和全音频人工听感验收；完整结果见
[docs/VALIDATION.md](docs/VALIDATION.md)。

## 发布与构建

维护者可按 [docs/BUILD_INSTALLER.md](docs/BUILD_INSTALLER.md) 安装 WiX v4 并构建 MSI。
发布前必须为 Python Embeddable、get-pip.py 和依赖 lock 填入官方 SHA-256；构建脚本会
拒绝占位哈希。未签名的本地构建可能显示 Windows SmartScreen 警告。

Linux 侧只提供 `packaging/linux/install.sh` 这一份可审计的源码安装脚本，没有二进制包，
因此不需要哈希清单。

## 首版边界

不包含波形编辑、日文 ASR 原文修改、Ollama 静默安装、无提示的外部服务回退或自动
下载模型。MSI 是轻量前端；运行时依赖必须由用户在向导中明确选择，或在设置中手动配置。

跨平台部分的边界：Linux 不提供 Flatpak、AppImage、`.deb` 或 `.rpm`；MSI 不打包 Qt
GUI；macOS 虽有路径分支但未验证 CUDA，不作为支持平台。

## 许可证

本项目整体采用 GNU Affero General Public License v3.0 or later，完整文本见 [COPYING](COPYING)。
Qt 6 以 LGPL-3.0 通过 PySide6-Essentials 动态链接使用，第三方组件的许可与署名见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
