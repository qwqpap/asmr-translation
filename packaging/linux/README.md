# Linux 安装（PySide6 GUI）

Linux 下没有原生 Win32 GUI，界面由 `asmr_gui` 提供：同一个 Python 进程直接调用
`asmr_lrc`，用 Qt 6（PySide6，LGPL-3.0）绘制任务、播放器、下载、设置四页，和 Windows
版共用同一份 `settings.json` 结构。

`install.sh` 只写入当前用户的家目录：一个私有 venv、一个 desktop 条目和一个图标。
不使用 sudo，不写系统目录，也不会替你安装发行版软件包或下载任何模型 —— 缺少的系统库
只会在安装过程中被检测并提示。

项目总览见 [../../README.md](../../README.md)，报错对照见
[../../docs/TROUBLESHOOTING.md](../../docs/TROUBLESHOOTING.md) 的“Linux 专属问题”一节。

## 1. 系统依赖

PySide6 的 wheel 自带 Qt 本体，但仍然依赖发行版的 xcb、EGL 和 fontconfig 库；音频
输出需要 PortAudio，解码需要 FFmpeg。

Debian / Ubuntu：

```bash
sudo apt install python3-venv python3-dev ffmpeg libportaudio2 \
  libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
  libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 libxcb-xkb1 \
  libxkbcommon-x11-0 libegl1 libgl1 libdbus-1-3 libfontconfig1
```

Fedora：

```bash
sudo dnf install python3-devel ffmpeg-free portaudio \
  xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm \
  libxkbcommon-x11 mesa-libEGL mesa-libGL dbus-libs fontconfig
```

官方源的 `ffmpeg-free` 去掉了部分解码器；遇到无法解码的音频可改用 RPM Fusion 的完整
`ffmpeg`。

Arch / Manjaro：

```bash
sudo pacman -S --needed ffmpeg portaudio \
  xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm \
  libxkbcommon-x11 libglvnd fontconfig
```

openSUSE Tumbleweed：

```bash
sudo zypper install python313 python313-devel ffmpeg-7 libportaudio2 \
  libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
  libxcb-render-util0 libxcb-shape0 libxcb-xkb1 libxkbcommon-x11-0 libglvnd \
  fontconfig
```

本项目要求 Python 3.12 或 3.13。滚动发行版的默认 `python3` 可能已经更新到更高版本，
`install.sh` 会先在 `python3.13`、`python3.12`、`python3` 中挑选可用的那个；都不满足时
装一个（Arch 可用 AUR 的 `python312`），再用 `ASMR_PYTHON` 指定：

```bash
ASMR_PYTHON=python3.13 ./packaging/linux/install.sh
```

## 2. 安装

```bash
git clone https://github.com/qwqpap/asmr-translation
cd asmr-translation
./packaging/linux/install.sh          # 需要 CUDA 时加 --cuda
```

脚本会：

1. 在 `${XDG_DATA_HOME:-~/.local/share}/asmr-translation/venv` 建 venv（`--prefix` 可改）；
2. `pip install .[gui]`（`--cuda` 时为 `.[gui,cuda]`）；
3. 检查 ffmpeg、PortAudio 和 Qt xcb 插件的动态库，缺什么只警告不安装；
4. 安装 `~/.local/share/applications/asmr-translation.desktop`、
   `~/.local/share/icons/hicolor/scalable/apps/asmr-translation.svg`
   和 `~/.local/bin/asmr-translation` 符号链接（`--no-desktop` 可跳过）。

之后从应用菜单启动，或直接运行：

```bash
~/.local/bin/asmr-translation
```

模型仍需自行准备，脚本不会下载：

```bash
# faster-whisper large-v3 目录（含 model.bin），在“设置 → ASR 模型目录”里指定
ollama pull translategemma:4b
ollama pull qwen3.5-9b-abliterated:latest
```

Ollama 本体请按 [官方文档](https://ollama.com/download/linux) 安装；本项目不会静默安装或
启动它。

## 3. 中文输入法

编辑台词需要输入法。Qt 从 `QT_IM_MODULE` 选择输入上下文插件：

```bash
export QT_IM_MODULE=fcitx    # ibus 用户写 ibus
```

wheel 里的 Qt 是官方构建，不带 fcitx5/ibus 插件，需要发行版提供 Qt 6 版本的那一个：

```bash
sudo apt install fcitx5-frontend-qt6      # Debian/Ubuntu
sudo dnf install fcitx5-qt6               # Fedora
sudo pacman -S --needed fcitx5-qt         # Arch
```

多数情况下系统插件能直接被 wheel 里的 Qt 加载。如果输入法唤不出来，先确认插件是否被
找到：

```bash
QT_LOGGING_RULES='qt.qpa.input*=true' ~/.local/bin/asmr-translation
```

日志显示插件缺失时，把系统插件软链进 wheel 的插件目录（路径里的 Qt 次版本号按实际
替换）：

```bash
venv=${XDG_DATA_HOME:-$HOME/.local/share}/asmr-translation/venv
target=$("$venv/bin/python" -c 'import PySide6,pathlib;print(pathlib.Path(PySide6.__file__).parent/"Qt"/"plugins"/"platforminputcontexts")')
ln -s /usr/lib/x86_64-linux-gnu/qt6/plugins/platforminputcontexts/libfcitx5platforminputcontextplugin.so "$target/"
```

系统插件和 wheel 的 Qt ABI 不兼容（跨大版本）时，改用发行版打包的 PySide6：建 venv 时
加 `--system-site-packages`，让 Qt 和输入法插件来自同一套构建。

## 4. X11 与 Wayland

播放器每 50 ms 刷新台词，两种会话都能正常工作。Wayland 会话下如果窗口起不来或输入法
行为异常，先看有哪些平台插件可用，再回退到 xcb：

```bash
ls ~/.local/share/asmr-translation/venv/lib/python3.*/site-packages/PySide6/Qt/plugins/platforms
QT_QPA_PLATFORM=xcb ~/.local/bin/asmr-translation
```

## 5. CUDA

`--cuda` 只安装 `nvidia-cublas-cu12` 和 `nvidia-cudnn-cu12` 这两个 pip wheel，NVIDIA
驱动仍由发行版提供。程序启动时按绝对路径 `dlopen` 这些库并导出 `LD_LIBRARY_PATH`，
供 ASR 子进程继承，因此不需要手工设置环境变量。装好后用探测确认：

```bash
~/.local/share/asmr-translation/venv/bin/python -m asmr_lrc --probe
```

`ctranslate2-cuda` 一项报告可见设备数为 0 时，先用 `nvidia-smi` 确认驱动，再检查驱动
版本是否支持 CUDA 12。

## 6. 凭据存储

OpenAI-compatible 的 API Key 通过 `keyring` 存入桌面钥匙环（GNOME Keyring、KWallet
等），不写进 `settings.json`、缓存、日志或子进程命令行。没有可用后端时设置页会明确
提示，而不是退化成明文保存。需要时安装：

```bash
sudo apt install gnome-keyring        # 或 kwalletmanager / libsecret 后端
```

只用本机 Ollama 的话不需要任何凭据后端。

## 7. 卸载

```bash
./packaging/linux/install.sh --uninstall
```

删除 venv、desktop 条目、图标和符号链接。以下目录保留，需要时自行删除：

```text
~/.config/asmr-translation          # settings.json，和 Windows 版结构一致
~/.cache/asmr-translation           # 转写与翻译缓存、播放器 WAV 代理
~/.local/state/asmr-translation     # 日志
```

（三者都遵循 `XDG_CONFIG_HOME` / `XDG_CACHE_HOME` / `XDG_STATE_HOME`。下载的作品默认在
`~/Downloads/ASMR Translation`，或 xdg-user-dirs 配置的下载目录下。）

## 8. 本版边界

- 不提供 Flatpak、AppImage、`.deb` 或 `.rpm`。沙箱内可靠地暴露 NVIDIA 驱动、本机
  Ollama 和用户自备的模型目录需要大量额外配置，与“不静默下载模型、不隐藏依赖”的取向
  冲突，所以这里只提供可审计的源码安装脚本。
- 不提供 macOS 支持：`asmr_lrc.platform_paths` 已有分支，但 CUDA 路径无从验证。
- 不打包 FFmpeg、PortAudio、Ollama 或 Whisper 权重。
