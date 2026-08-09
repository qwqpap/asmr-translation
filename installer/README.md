# WiX MSI 发布目录

这个目录只保存可复现的安装器输入，不保存 Python、CUDA、FFmpeg 或 Whisper
模型。发布构建会把 `native/build/Release/asmr-translation.exe`、项目 wheel、
依赖清单和文档复制到临时 staging 目录，再交给 WiX 生成当前用户范围的 MSI。

安装器不会静默联网。首次启动时由 GUI 打开依赖向导，只有用户勾选并点击安装后
才执行 `bootstrap.ps1`。`manifest/artifacts.json` 中的每一项都必须填入官方或
用户指定镜像的 URL、大小和 SHA-256；构建脚本会拒绝 `TODO`、全零或缺失哈希。

固定 Python 和 `get-pip.py` 后可运行 `update-manifest.ps1` 自动计算本地文件哈希；
依赖 lock 仍必须由维护者生成并逐项固定。
