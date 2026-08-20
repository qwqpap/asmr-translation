# 构建 WiX MSI（维护者）

MSI 只打包原生 Win32 GUI。跨平台 Qt GUI 不进安装包，也不进依赖 lock：Qt 约 100MB，
对只用原生 GUI 的用户是纯负担，需要它的人按
[packaging/linux/README.md](../packaging/linux/README.md) 或 README 的手工环境一节装
`.[gui]`。Linux 侧没有二进制包，只有 `packaging/linux/install.sh`，因此不需要哈希清单
和签名流程。

## 工具链

- Windows 10/11 x64
- Visual Studio 2022 x64 C++ 工具链
- CMake 3.24+
- Python 3.12 或 3.13 和项目开发依赖（MSI 引导的运行时仍固定为 3.12 Embeddable）
- WiX Toolset v4，命令为 `wix`

仓库当前不捆绑 WiX。可使用官方 .NET tool 安装到用户范围，再确认 `wix --version`。

## 发布前准备

1. 下载固定版本的 Python Embeddable 和 `get-pip.py`，可用
   `installer/update-manifest.ps1` 写入官方 URL、大小和 SHA-256。
2. 生成完整的 Python 3.12 win_amd64 CPU/CUDA lock，所有包都必须有 `--hash`。
3. 确认 `installer/manifest/artifacts.json` 中没有 `REPLACE_`、`TODO` 或全零哈希。
4. 构建项目 wheel，并让 wheel 版本与 `pyproject.toml`、`asmr_lrc.__version__`、MSI
   版本一致。

示例：

```powershell
python -m pip install build
python -m build --wheel
cmake -S native -B native/build-installer -G "Visual Studio 17 2022" -A x64
cmake --build native/build-installer --config Release
.\installer\build.ps1 -Version 0.3.0
```

脚本会复制 GUI、wheel、引导脚本、manifest、lock 和许可证到临时 staging，生成
`dist/asmr-translation-0.3.0-x64.msi`，并打印 MSI SHA-256。无 WiX 或占位哈希时会
在构建开始前失败，不会生成不可用安装包。

## Manifest 与镜像规则

`bootstrap.ps1` 只接受 manifest 中的文件，先下载到 `%LocalAppData%\ASMR Translation\downloads`
再校验哈希并原子改名。用户镜像只替换 manifest URL 的主机/路径前缀，不改变预期文件名
和哈希，也不触发自动 fallback。压缩包解压前拒绝绝对路径、`..` 和越过目标根目录的条目。

模型和 FFmpeg 可以作为可选 manifest 条目发布；Whisper 模型在 GUI 中默认不勾选。Ollama
不放入 MSI，也不由引导脚本安装。

## 验证与签名

发布前依次运行：

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
cmake --build native/build-installer --config Release
ctest --test-dir native/build-installer -C Release --output-on-failure
```

在干净的普通用户账户测试安装、首次向导、取消/恢复、错误哈希、镜像、升级、修复和
卸载；确认卸载不删除 `%LocalAppData%\ASMR Translation` 或下载资料库。签名证书只从
发布机安全存储读取，不提交仓库；未签名 MSI 需要在发布说明中标注 SmartScreen 提示。
