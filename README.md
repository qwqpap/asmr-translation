# ASMR Translation v0.3

Windows 本地日语 ASMR 转写、上下文翻译与同步台词播放器。

源码仓库：[github.com/qwqpap/asmr-translation](https://github.com/qwqpap/asmr-translation)。

- Python 核心使用 `faster-whisper` 转写，默认通过本机 Ollama 翻译。
- C++20 原生 Win32 GUI 提供任务、播放器、下载和设置四页，不依赖 Qt/WPF。
- 质量模式执行语境预分析、12 行批量初译和全量终审；平衡模式保留单遍流程。
- 可分别为初译和终审选择 Ollama 或 OpenAI-compatible `/v1/chat/completions`。
- 音频默认不上传；外部 API 只发送用户明确授权的转写文本。

## 主要能力

- 前后各 8 行只读上下文、人物/风格/话题记忆和带证据 ID 的术语表。
- 精确 ID 动态 JSON Schema，校验缺失、额外、重复、合并、空译文和日文残留。
- 有反馈的有限重试、失败批次二分恢复和单 ID 术语修复。
- draft/review 分阶段缓存与 `review_changed`、`asr_suspect`、`term_conflict`、
  `term_repaired`、`low_confidence` 标志。
- UTF-8 JSONL GUI worker：`probe`、`run`、`load_cues`、`save_edits`、
  `prepare_playback`、`download_plan`、`download_run`。
- Media Foundation 播放、跳转、音量、0.75–2.0 倍速、播放列表和 50 ms 台词刷新。
- 中文主行、日文副行、点击跳转、双击编辑；首次编辑备份 `.lrc.bak`，之后原子保存。
- 系统不支持的格式可由 FFmpeg 生成 PCM WAV 代理，默认 4 GiB LRU 缓存。
- 协作取消后由 Win32 Job Object 兜底清理 Python/ASR/curl 子进程树。

## RJ 一站式下载

下载页接受 `RJ01528633`、数字 RJ 编号或 DLsite 作品链接。默认资料库为
`%USERPROFILE%\\Downloads\\ASMR Translation`，默认只选择时长和文件名可靠匹配的较小音频版本；
可以改为全部音频、全部文件或手动勾选。文件使用 `.part` 和 HTTP Range 续传，完成后校验大小并原子改名，
每个作品保存 `download.manifest.json`。endpoint、curl、代理和连接超时均可在设置页修改，不会静默切换镜像。

下载功能改编自 [thiliapr/asmr-one-downloader](https://github.com/thiliapr/asmr-one-downloader)，
保留其作者署名、AGPL-3.0-or-later 许可和源码链接，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
请只下载你有权访问的内容；本项目不处理账号、Cookie、付费登录或访问限制绕过。

## 安全边界

- 不修改、重编码或删除原音频。
- 默认不覆盖已有 LRC；CLI 只有 `--overwrite` 才覆盖。
- Whisper 转写缓存与翻译 v2 缓存分离，升级提示词不会重跑昂贵 ASR。
- GUI 的 API Key 存入 Windows Credential Manager，不进入设置、缓存、日志或命令行。
- CLI 的外部 Key 只从指定环境变量读取；非交互外发还需 `--allow-external-text`。
- 不静默切换模型，不自动下载 Python、CUDA、FFmpeg、Ollama 或模型。

## 环境与安装

要求 Windows 10/11、Python 3.12、FFmpeg；本地翻译还需要 Ollama。CUDA 为当前
`large-v3` 实机配置，CPU 仅适合诊断。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,cuda]"
.\.venv\Scripts\python.exe -m asmr_lrc --probe
```

项目不会自动下载模型。准备好的 faster-whisper 目录应包含 `model.bin`；浏览器有时会把
二进制权重误存为 `.mht`，确认文件头与目标后只需改名，不要转换内容。
原生 GUI 会自动使用项目下的 `models/faster-whisper-large-v3`，也可在“设置 → ASR
模型目录”中改为其他完整的 faster-whisper 模型目录。

## 构建与启动原生 GUI

使用 Visual Studio 2022 的 x64 C++ 工具链：

```powershell
cmake -S native -B native/build -G "Visual Studio 17 2022" -A x64
cmake --build native/build --config Release
.\native\build\Release\asmr-translation.exe
```

也可把音频路径作为 EXE 参数，或拖放到窗口。GUI 会向上寻找项目 `.venv`，并从 PATH
查找 FFmpeg；路径可在设置页修正。

## CLI 用法

```powershell
# 计划与环境
.\.venv\Scripts\python.exe -m asmr_lrc "D:\ASMR" --dry-run
.\.venv\Scripts\python.exe -m asmr_lrc --probe

# 默认质量模式
.\.venv\Scripts\python.exe -m asmr_lrc "D:\ASMR" --quality-mode quality

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

翻译 v2 profile 包含提供方、模型、提示词版本、上下文策略、审校模式和术语哈希，不含
API Key。损坏缓存会隔离为 `*.corrupt-N`，旧 Schema 会保留为 `*.stale-N`。

## 验证

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
cmake --build native/build --config Release
ctest --test-dir native/build -C Release --output-on-failure
.\.venv\Scripts\python.exe tools\evaluate_quality_cases.py
```

质量 fixture 的可翻译项为 24/24，四个严重案例全部通过；结构通过仍不代替全音频人工
听感验收。当前实机证据与尚未完成的视觉门槛见 [docs/VALIDATION.md](docs/VALIDATION.md)。

## 首版边界

不包含波形编辑、日文 ASR 原文修改、自包含安装包、自动模型下载或无提示的外部服务
回退。EXE 是轻量前端，运行时仍需项目 Python 环境和相应本地工具。

## 许可证

本项目整体采用 GNU Affero General Public License v3.0 or later，完整文本见 [COPYING](COPYING)。
