"""Settings page.

API keys are handled apart from everything else on purpose: they go straight to
the OS credential store (Windows Credential Manager, or Secret Service / Keychain
through ``keyring``) and never into ``settings.json``.  The field shows a
placeholder rather than the stored secret, so a screenshot of this page cannot
leak a key.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from asmr_lrc import credentials
from asmr_lrc.platform_paths import describe_platform, settings_path

from ..settings import AppSettings, ProviderSettings, save_settings

_ROLE_LABELS = {
    "draft": "初译",
    "review": "审校",
    "analysis": "语境分析",
    "fallback": "回退",
}
_KIND_LABELS = (("Ollama（本地）", "ollama"), ("OpenAI 兼容（远程）", "openai"))
_PROTOCOLS = ("chat-json", "translategemma")
_SECRET_PLACEHOLDER = "（已保存，留空表示不修改）"


class _PathRow(QWidget):
    """A text field plus a browse button, for files or directories."""

    def __init__(self, *, directory: bool, caption: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.edit = QLineEdit()
        button = QPushButton("浏览…")
        button.clicked.connect(self._browse)
        self._directory = directory
        self._caption = caption
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(button)

    def _browse(self) -> None:
        current = self.edit.text().strip()
        if self._directory:
            chosen = QFileDialog.getExistingDirectory(self, self._caption, current)
        else:
            chosen, _ = QFileDialog.getOpenFileName(self, self._caption, current)
        if chosen:
            self.edit.setText(chosen)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, value: str) -> None:  # noqa: N802 - mirrors QLineEdit
        self.edit.setText(value)


class _ProviderBox(QGroupBox):
    """Editor for one translation role, with its own credential field."""

    def __init__(self, role: str, parent: QWidget | None = None) -> None:
        super().__init__(_ROLE_LABELS.get(role, role), parent)
        self.role = role
        self.kind = QComboBox()
        for label, value in _KIND_LABELS:
            self.kind.addItem(label, value)
        self.base_url = QLineEdit()
        self.model = QLineEdit()
        self.protocol = QComboBox()
        self.protocol.addItems(_PROTOCOLS)
        self.strict_schema = QCheckBox("要求严格 JSON Schema")
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("仅远程服务需要；保存到系统凭据库")
        self.key_hint = QLabel()
        self.key_hint.setWordWrap(True)

        layout = QFormLayout(self)
        layout.addRow("类型", self.kind)
        layout.addRow("Base URL", self.base_url)
        layout.addRow("模型", self.model)
        layout.addRow("协议", self.protocol)
        layout.addRow("", self.strict_schema)
        layout.addRow("API Key", self.api_key)
        layout.addRow("", self.key_hint)
        self.kind.currentIndexChanged.connect(self._sync_enabled)

    def _sync_enabled(self) -> None:
        remote = self.kind.currentData() == "openai"
        self.api_key.setEnabled(remote)
        variable = credentials.environment_variable_for_role(self.role)
        self.key_hint.setText(
            f"也可用环境变量 {variable} 覆盖。密钥不会写入 settings.json。"
            if remote
            else "本地 Ollama 不需要密钥。"
        )

    def load(self, provider: ProviderSettings) -> None:
        index = max(0, self.kind.findData(provider.kind))
        self.kind.setCurrentIndex(index)
        self.base_url.setText(provider.base_url)
        self.model.setText(provider.model)
        protocol_index = self.protocol.findText(provider.protocol)
        self.protocol.setCurrentIndex(protocol_index if protocol_index >= 0 else 0)
        self.strict_schema.setChecked(provider.strict_schema)
        self.api_key.clear()
        stored = credentials.read_secret(self.role)
        self.api_key.setPlaceholderText(
            _SECRET_PLACEHOLDER if stored else "仅远程服务需要；保存到系统凭据库"
        )
        self._sync_enabled()

    def collect(self) -> ProviderSettings:
        return ProviderSettings(
            kind=str(self.kind.currentData()),
            base_url=self.base_url.text().strip(),
            model=self.model.text().strip(),
            strict_schema=self.strict_schema.isChecked(),
            protocol=self.protocol.currentText(),
        )

    def pending_secret(self) -> str | None:
        """Return the typed key, or ``None`` when the stored one should stay."""
        text = self.api_key.text()
        return text if text else None


class SettingsPage(QWidget):
    """Edits the same ``settings.json`` the native GUI reads and writes."""

    def __init__(self, settings: AppSettings, on_saved, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._on_saved = on_saved

        self.asr_model = _PathRow(directory=True, caption="选择 faster-whisper 模型目录")
        self.ffmpeg_path = _PathRow(directory=False, caption="选择 ffmpeg 可执行文件")
        self.cache_root = _PathRow(directory=True, caption="选择缓存目录")
        self.glossary_path = _PathRow(directory=False, caption="选择术语表 JSON")
        self.download_root = _PathRow(directory=True, caption="选择下载目录")

        self.quality_mode = QCheckBox("质量优先（启用语境分析）")
        self.review_enabled = QCheckBox("启用审校阶段")
        self.review_same = QCheckBox("审校复用初译配置")
        self.analysis_enabled = QCheckBox("启用语境分析模型")
        self.fallback_enabled = QCheckBox("启用回退模型")

        self.endpoint = QLineEdit()
        self.curl_path = _PathRow(directory=False, caption="选择 curl 可执行文件")
        self.proxy = QLineEdit()
        self.proxy.setPlaceholderText("例如 http://127.0.0.1:7890，留空表示直连")
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 300)
        self.timeout.setSuffix(" 秒")

        self.providers = {role: _ProviderBox(role) for role in credentials.roles()}

        paths_box = QGroupBox("路径")
        paths_form = QFormLayout(paths_box)
        paths_form.addRow("ASR 模型", self.asr_model)
        paths_form.addRow("FFmpeg", self.ffmpeg_path)
        paths_form.addRow("缓存目录", self.cache_root)
        paths_form.addRow("术语表", self.glossary_path)
        paths_form.addRow("下载目录", self.download_root)

        pipeline_box = QGroupBox("流程")
        pipeline_layout = QVBoxLayout(pipeline_box)
        for widget in (
            self.quality_mode,
            self.review_enabled,
            self.review_same,
            self.analysis_enabled,
            self.fallback_enabled,
        ):
            pipeline_layout.addWidget(widget)

        download_box = QGroupBox("下载")
        download_form = QFormLayout(download_box)
        download_form.addRow("API 端点", self.endpoint)
        download_form.addRow("curl", self.curl_path)
        download_form.addRow("代理", self.proxy)
        download_form.addRow("连接超时", self.timeout)

        provider_tabs = QTabWidget()
        for role, box in self.providers.items():
            provider_tabs.addTab(box, _ROLE_LABELS.get(role, role))

        self.backend_label = QLabel()
        self.backend_label.setWordWrap(True)
        self.location_label = QLabel()
        self.location_label.setWordWrap(True)

        save_button = QPushButton("保存设置")
        save_button.clicked.connect(self._save)
        reset_button = QPushButton("恢复默认")
        reset_button.clicked.connect(self._reset)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(reset_button)
        buttons.addWidget(save_button)

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.addWidget(paths_box)
        inner_layout.addWidget(pipeline_box)
        inner_layout.addWidget(provider_tabs)
        inner_layout.addWidget(download_box)
        inner_layout.addWidget(self.backend_label)
        inner_layout.addWidget(self.location_label)
        inner_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addLayout(buttons)

        self.load(settings)

    # --- state -------------------------------------------------------------

    def load(self, settings: AppSettings) -> None:
        self._settings = settings
        self.asr_model.setText(settings.asr_model)
        self.ffmpeg_path.setText(settings.ffmpeg_path)
        self.cache_root.setText(settings.cache_root)
        self.glossary_path.setText(settings.glossary_path)
        self.download_root.setText(settings.download_root)
        self.quality_mode.setChecked(settings.quality_mode)
        self.review_enabled.setChecked(settings.review_enabled)
        self.review_same.setChecked(settings.review_same_as_draft)
        self.analysis_enabled.setChecked(settings.analysis_enabled)
        self.fallback_enabled.setChecked(settings.fallback_enabled)
        self.endpoint.setText(settings.download_endpoint)
        self.curl_path.setText(settings.curl_path)
        self.proxy.setText(settings.download_proxy)
        self.timeout.setValue(settings.download_connect_timeout)
        for role, box in self.providers.items():
            box.load(getattr(settings, role))
        status = credentials.backend_status()
        self.backend_label.setText(
            f"凭据后端: {status.name} — {status.detail}"
            if status.available
            else f"凭据后端不可用: {status.detail}\n可改用环境变量提供密钥。"
        )
        self.location_label.setText(f"平台: {describe_platform()}\n配置文件: {settings_path()}")

    def collect(self) -> AppSettings:
        return replace(
            self._settings,
            asr_model=self.asr_model.text(),
            ffmpeg_path=self.ffmpeg_path.text(),
            cache_root=self.cache_root.text(),
            glossary_path=self.glossary_path.text(),
            download_root=self.download_root.text(),
            quality_mode=self.quality_mode.isChecked(),
            review_enabled=self.review_enabled.isChecked(),
            review_same_as_draft=self.review_same.isChecked(),
            analysis_enabled=self.analysis_enabled.isChecked(),
            fallback_enabled=self.fallback_enabled.isChecked(),
            download_endpoint=self.endpoint.text().strip(),
            curl_path=self.curl_path.text(),
            download_proxy=self.proxy.text().strip(),
            download_connect_timeout=self.timeout.value(),
            draft=self.providers["draft"].collect(),
            review=self.providers["review"].collect(),
            analysis=self.providers["analysis"].collect(),
            fallback=self.providers["fallback"].collect(),
        )

    def _save(self) -> None:
        updated = self.collect()
        cache_root = Path(updated.cache_root).expanduser() if updated.cache_root else None
        if cache_root is not None and cache_root.exists() and not cache_root.is_dir():
            QMessageBox.warning(self, "缓存目录无效", f"{cache_root} 不是目录。")
            return
        failures: list[str] = []
        for role, box in self.providers.items():
            secret = box.pending_secret()
            if secret is None:
                continue
            try:
                credentials.write_secret(role, secret)
            except credentials.CredentialError as exc:
                failures.append(f"{_ROLE_LABELS.get(role, role)}: {exc}")
        try:
            path = save_settings(updated)
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", f"无法写入设置: {exc}")
            return
        self._settings = updated
        for box in self.providers.values():
            box.api_key.clear()
            box.load(getattr(updated, box.role))
        self._on_saved(updated)
        if failures:
            QMessageBox.warning(
                self,
                "密钥未保存",
                "设置已保存，但以下密钥写入失败：\n" + "\n".join(failures),
            )
        else:
            QMessageBox.information(self, "已保存", f"设置已写入\n{path}")

    def _reset(self) -> None:
        reply = QMessageBox.question(
            self,
            "恢复默认",
            "将表单恢复为自动探测的默认值？已保存的密钥不会被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.load(AppSettings.defaults())
