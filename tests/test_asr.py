from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from asmr_lrc.asr import run_asr_process


def test_asr_child_does_not_inherit_gui_control_stdin(
    tmp_path: Path, monkeypatch: Any
) -> None:
    captured: dict[str, object] = {}

    class CompletedProcess:
        returncode = 0

        def poll(self) -> int:
            return 0

        def communicate(self) -> tuple[str, str]:
            return "", ""

    def fake_popen(command: list[str], **kwargs: object) -> CompletedProcess:
        captured["command"] = command
        captured.update(kwargs)
        return CompletedProcess()

    monkeypatch.setattr("asmr_lrc.asr.subprocess.Popen", fake_popen)
    output = tmp_path / "transcript.raw.json"
    output.write_text("{}", encoding="utf-8")
    model = str(tmp_path / "models" / "faster-whisper-large-v3")

    run_asr_process(
        tmp_path / "audio.mp3",
        output,
        tmp_path / "process.log",
        model=model,
        device="cuda",
        compute_type="int8_float16",
        ollama_url=None,
    )

    assert captured["stdin"] == subprocess.DEVNULL
    assert model in captured["command"]
