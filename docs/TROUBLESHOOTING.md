# 故障排查

Windows 有原生 Win32 GUI 和跨平台 Qt GUI 两个前端，Linux 只有 Qt GUI。除注明平台的
条目外，下面的内容对两者都适用。

## 按钮没有反应或任务立即结束

先打开“任务 → 环境探测”。如果 Python 路径显示为 `python` 或文件不存在，打开
“设置 → 依赖向导”完成嵌入式 Python/ASR 安装；开发环境则填写
`.venv\Scripts\python.exe`。日志和设置位于 `%LocalAppData%\ASMR Translation`
（Linux 为 `~/.config/asmr-translation` 和 `~/.local/state/asmr-translation`）。

如果 worker 启动后立即退出，确认路径指向 `python.exe` 而不是目录，并从设置页保存一次。
安装版不要把 Python 路径改成 MSI 安装目录中的不存在文件。Qt GUI 在同一进程内直接调用
`asmr_lrc`，不经过 worker 进程，因此这一项只影响原生 GUI。

## Qt GUI 打不开：`DLL load failed while importing QtCore`（Windows）

症状是 `import PySide6` 抛出没有更多说明的 `ImportError`，`WinError 127`（找不到指定的
程序）。原因是 `Qt6Core.dll` 按无版本号的 `icuuc.dll` 链接系统 ICU，而 Anaconda /
Miniconda 在 `<prefix>\Library\bin` 下自带一份带版本号符号（`ucnv_open_73`）的 ICU；
只要解释器来自 conda，这个目录就排在 `System32` 之前，加载器会把错误的
`icuuc.dll` 交给 Qt。

`asmr_gui/qt_bootstrap.py` 在包导入时先加载 `%SystemRoot%\System32\icuuc.dll`，把这个
名字钉死在进程里，所以正常从 `asmr_gui`（`asmr-translation` 命令、`python -m asmr_gui`）
启动不会遇到。只有绕过它直接 `import PySide6` 时才会复现：

```powershell
# 复现
.\.venv\Scripts\python.exe -c "from PySide6 import QtWidgets"
# 修好之后的等价写法
.\.venv\Scripts\python.exe -c "import asmr_gui; from PySide6 import QtWidgets"
```

如果仍然失败，检查 PATH 里是否有第三方软件安装的 `Qt6Core.dll`（`where Qt6Core.dll`），
它会和 wheel 自带的 Qt 混用。同一目录下另装的 Qt 不受本项目控制，只能把它从 PATH 移除。

## 没有 GPU 或 CUDA 错误

- 在 NVIDIA 驱动正常且 `nvidia-smi` 可用后，重新运行环境探测。
- 向导选择 CUDA 时必须使用兼容的 NVIDIA 驱动；否则选择 CPU 依赖完成诊断。
- 显存不足时降低 ASR 模型或改用 CPU；程序不会静默切换模型。
- `model.bin` 是二进制文件。浏览器若保存为 `.mht`，确认文件头和来源后只改扩展名，
  不要转换内容。
- Linux 上 pip 装的 CUDA 库由程序按绝对路径 `dlopen` 并导出 `LD_LIBRARY_PATH`，
  不需要手工设置。`ctranslate2-cuda` 仍报 0 个设备时，先确认发行版驱动支持 CUDA 12。

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

Qt GUI 的播放器自己解码 PCM 并通过 PortAudio 输出，因此每种格式都会先转成 WAV 代理。
`sounddevice` 缺失或没有可用输出设备时，只有播放页不可用，转写和翻译照常工作。

## 下载失败、镜像和断点续传

- 确认 endpoint、代理和连接超时；不要填写带临时签名参数的 URL 到日志或设置。
- 401/403/404 或错误 manifest 会立即失败；429、5xx、断流和超时使用有限重试。
- `.part` 文件会按大小继续下载。哈希不匹配时临时文件会删除，不覆盖已有完整文件。
- 镜像必须由用户明确填写；程序不会在失败后自动尝试未知镜像。
- 取消下载后等待任务退出，再次启动即可恢复；必要时删除对应 `.part` 后重新开始。

## Linux 专属问题

安装步骤和各发行版包名见 [packaging/linux/README.md](../packaging/linux/README.md)。

- **窗口完全起不来，报 `Could not load the Qt platform plugin "xcb"`**：缺少 xcb 系列
  系统库，最常见的是 `libxcb-cursor0`（Fedora/Arch 叫 `xcb-util-cursor`）。用
  `QT_DEBUG_PLUGINS=1` 启动可以看到具体缺哪个 `.so`。
- **有窗口但没有声音**：`python -c "import sounddevice"` 失败说明缺 `libportaudio2`。
  安装后重新探测，“audio-output”一项会显示默认设备名。
- **中文输入法唤不出来**：设置 `QT_IM_MODULE=fcitx`（或 `ibus`），并安装发行版的 Qt 6
  前端（`fcitx5-frontend-qt6` / `fcitx5-qt6` / `fcitx5-qt`）。wheel 里的 Qt 是官方构建，
  跨大版本时系统插件可能加载不上，此时改用发行版打包的 PySide6（建 venv 时加
  `--system-site-packages`）。
- **Wayland 下窗口或输入法异常**：先用 `QT_QPA_PLATFORM=xcb` 启动，确认是否与合成器相关。
- **`python3 -m venv` 失败**：Debian/Ubuntu 需要单独的 `python3-venv` 包。
- **API Key 保存失败**：`keyring` 找不到可用后端（GNOME Keyring、KWallet 等）。设置页会
  明确提示，不会退化成明文保存；只用本机 Ollama 时不需要任何凭据后端。

## 外部 API 的数据范围

外部审校只接收转写文本、目标行、上下文和术语，不接收音频文件。首次使用前会显示
预计字符量；取消授权即可保持本地 Ollama 流程。

## 收集诊断信息

提交问题时请附上：操作系统与版本（Linux 另附桌面环境和会话类型）、GPU/驱动、
环境探测结果、相关日志和复现步骤。请先
删除 API Key、Cookie、签名下载 URL 及个人路径；不要上传原始音频或整份 Credential。
