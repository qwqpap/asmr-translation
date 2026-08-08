# ASMR LRC

一个完全在 Windows 本机运行的命令行工具：使用 `faster-whisper` 将日语 ASMR
音频转写为带时间戳的字幕，再通过本机 Ollama 翻译成简体中文，最终在音频旁安全地
生成同名 `.lrc`。音频、转写和译文不会上传到在线服务。

## 安全边界

- 默认不覆盖已有 LRC；只有 `--overwrite` 会覆盖。
- 永不修改、重编码或删除原音频。
- 每次只运行一个 ASR 子进程。
- 批次中的所有 ASR 都完成并退出后，才开始调用 Ollama；两种模型不会由本工具同时加载。
- ASR 前若发现 `ollama ps` 中有模型，本工具会停止并给出卸载提示。
- 缓存清理必须由用户手动执行，本工具不自动删除中间数据。

## 安装

要求 Windows、Python 3.12、FFmpeg、NVIDIA 驱动/CUDA 运行能力和 Ollama。
不要使用全局 Anaconda 环境作为最终运行环境。

```powershell
cd C:\path\to\translate
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

GPU 环境应安装 CUDA 运行时额外依赖：

```powershell
python -m pip install -e ".[dev,cuda]"
```

如果本机 pip 配置的镜像长时间无响应，可临时指定可用镜像：

```powershell
python -m pip install --index-url https://mirrors.aliyun.com/pypi/simple -e ".[dev,cuda]"
```

如果 PowerShell 禁止激活脚本，可以直接使用
`.\.venv\Scripts\python.exe`，无需修改系统执行策略。

安装或准备本地翻译模型：

```powershell
ollama pull qwen3.5-9b-abliterated:latest
ollama list
```

如果手动下载 `Systran/faster-whisper-large-v3`，目录应包含 `model.bin`。某些浏览器会
把这个二进制权重错误保存成 `.mht`；只要文件头是 `WhisperSpec`，可在确认目标不存在后
将它改名为 `model.bin`，不要重新编码文件。使用项目内模型时：

```powershell
python -m asmr_lrc "D:\ASMR" --asr-model ".\models\faster-whisper-large-v3"
```

模型名称不是硬编码的，可通过 `--ollama-model` 替换。修改版模型尚未完成真实 ASMR
质量验收前，不应把“JSON 格式正确”等同于翻译质量合格。

## 首次运行

先探测环境：

```powershell
python -m asmr_lrc --probe
```

确认将处理哪些文件（不创建缓存，不写 LRC）：

```powershell
python -m asmr_lrc "D:\ASMR 中文 日本語" --dry-run
```

开始完整批处理：

```powershell
python -m asmr_lrc "D:\ASMR 中文 日本語"
```

常用选项：

```powershell
python -m asmr_lrc "D:\ASMR" --transcribe-only
python -m asmr_lrc "D:\ASMR" --translate-only
python -m asmr_lrc "D:\ASMR" --overwrite
python -m asmr_lrc "D:\ASMR" --asr-model medium
python -m asmr_lrc "D:\ASMR" --fallback-asr-model medium
python -m asmr_lrc "D:\ASMR" --ollama-model <model-name>
```

`--fallback-asr-model medium` 只会在首选模型失败后额外尝试一次，并将过程写入日志；
默认不会静默降级或无限重试。完整参数见 `python -m asmr_lrc --help`。

## 缓存与断点续跑

默认缓存位于当前工作目录 `.cache`，可用 `--cache-dir` 更改。每个音频按绝对路径和
内容指纹映射到独立目录：

```text
.cache/<path-hash>-<content-hash>/
  source.json
  transcript.raw.json
  transcript.filtered.json
  translation.zh-CN.json
  process.log
```

有效转写与译文会自动复用。源文件大小、修改时间或内容改变后，缓存不会被误用。
结构损坏的缓存会重命名为 `*.corrupt-N`，随后安全重建；`--translate-only` 下缺少有效
转写缓存会明确失败。

手动清理项目缓存：

```powershell
Remove-Item -LiteralPath ".\.cache" -Recurse -Force
```

卸载项目环境：先确认当前目录，再删除项目内的 `.venv`。这些操作不可恢复，命令不会
由程序自动执行。

## 常见问题

### CUDA 不可用

运行 `python -m asmr_lrc --probe`。若 `ctranslate2-cuda` 失败，检查 NVIDIA 驱动、
当前 Python 是否确实来自项目 `.venv`，以及安装的 CTranslate2 wheel 是否支持本机。
本项目第一版以 CUDA 为验收目标，`--device cpu` 只用于诊断，不代表性能验收通过。

### 显存不足

先运行 `ollama ps`，对其中每个模型执行 `ollama stop <模型名>`。关闭其他占用显存的
程序后重试。若 `large-v3` 仍 OOM，可显式使用 `--asr-model medium`，或指定
`--fallback-asr-model medium`。不要仅凭成功启动就宣布 6GB 配置稳定；应记录峰值显存、
耗时、实时倍率并抽听结果。

### Ollama 未启动或模型不存在

启动 Ollama 后运行 `ollama list`。用 `ollama pull <模型名>` 安装模型，或通过
`--ollama-model` 选择已安装模型。默认地址可用 `--ollama-url` 覆盖。

### 批次部分失败

单文件失败不会中止后续文件。退出码 `0` 表示无失败，`1` 表示部分成功，`2` 表示
全部尝试均失败或参数/环境错误。每个缓存目录的 `process.log` 保存调试信息，终端只显示
可操作的摘要。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
```

自动化测试、现场环境探测、真实音频性能测试和人工听感验收是四种不同证据。当前实机
记录见 [docs/VALIDATION.md](docs/VALIDATION.md)。
