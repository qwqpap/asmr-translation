# Windows 安装与首次运行

## MSI 安装

1. 从 GitHub Releases 下载与系统匹配的 `asmr-translation-0.3.0-x64.msi`。
2. 双击安装。安装范围是当前用户，默认目录为
   `%LocalAppData%\Programs\ASMR Translation`，不需要管理员权限。
3. 从开始菜单启动 **ASMR Translation**。首次启动会显示依赖向导。

MSI 只包含 Win32 GUI、项目 wheel、引导脚本、依赖清单和许可证，不包含约 2.4GB 的
Python 环境、CUDA、FFmpeg、Ollama 或约 3.1GB Whisper 模型，因此安装包本身很小。

## 依赖向导

向导不会在打开时联网。勾选项目并点击“安装所选”后才开始下载：

- Python 3.12 Embeddable 和 ASR Python 依赖默认勾选；可选择 CPU 或 NVIDIA CUDA 12。
- FFmpeg 是可选项。已有 `ffmpeg.exe` 时直接在设置中指定路径即可。
- Whisper `large-v3` 模型默认不勾选，模型下载完成后才会写入 ASR 模型路径。
- Ollama 不由本程序静默安装；请自行安装并在设置中确认服务地址和模型。环境探测会
  分别检查 `translategemma:4b`（主翻译）和
  `qwen3.5-9b-abliterated:latest`（语境/兜底），缺失时只显示精确的
  `ollama pull <model>` 命令；模型仍使用 Ollama 当前 C 盘目录。
- GTX 1660 Ti 6GB 按阶段使用显存：Whisper 结束后才加载 Qwen，语境完成后卸载 Qwen
  再加载 TranslateGemma；失败批次才重新加载 Qwen，不会三个模型同时常驻。

下载项均来自 manifest 中的固定 URL，并检查大小和 SHA-256。镜像框可以填写一个明确的
Base URL；程序不会因为失败而自动切换未知镜像。下载使用 `.part` 和 Range 续传，取消
后可重新打开向导继续。安装过程中不会发送音频或 API Key。

向导完成后，运行时和设置位于 `%LocalAppData%\ASMR Translation`：

```text
runtime\python-3.12-embed-amd64\python.exe
downloads\                 # 依赖缓存和 .part 文件
settings.json
cache\                     # 翻译/播放器缓存（可在设置中修改）
```

下载页的作品资料库默认是 `%USERPROFILE%\Downloads\ASMR Translation`，不会因为卸载
程序而删除。

## 更新、修复与卸载

- 新版本 MSI 使用同一升级代码，覆盖程序文件但保留设置、运行时、缓存和下载作品。
- “应用和功能”中的修复只恢复程序文件，不重置依赖向导状态。
- 卸载只移除安装目录和开始菜单快捷方式。若要清理运行时或缓存，请在确认备份后
  手动删除 `%LocalAppData%\ASMR Translation`；下载作品需要单独清理。
- 用户 API Key 存在 Windows Credential Manager，不在 `settings.json` 或日志中。

## 隐私与许可

本地 ASR 不上传音频。只有用户明确选择外部翻译提供方并确认授权时，才发送转写后的
日文及上下文。下载页只应下载你有权访问的作品，并遵守服务条款。

项目采用 AGPL-3.0-or-later；安装目录中包含 `COPYING` 和
`THIRD_PARTY_NOTICES.md`。
