from __future__ import annotations

import json
from pathlib import Path

import pytest

from asmr_lrc.downloader import (
    DownloadConfig,
    DownloadError,
    RemoteFile,
    WorkPlan,
    download_plan,
    normalize_rj,
    safe_relative_path,
    smart_audio_selection,
)
from asmr_lrc.gui_worker import dispatch


def remote(file_id: str, path: str, *, duration: float | None = 10.0) -> RemoteFile:
    return RemoteFile(file_id, path, "audio", 4, duration, "", "https://example.test/file")


def test_rj_and_windows_path_validation() -> None:
    assert normalize_rj("RJ01528633") == "1528633"
    assert normalize_rj("https://www.dlsite.com/maniax/work/RJ01528633.html") == "1528633"
    with pytest.raises(ValueError):
        normalize_rj("https://example.test/RJ01528633")
    with pytest.raises(DownloadError):
        safe_relative_path("C:/outside.mp3")
    with pytest.raises(DownloadError):
        safe_relative_path("music/../outside.mp3")
    with pytest.raises(DownloadError):
        safe_relative_path("music/./outside.mp3")
    assert safe_relative_path("mp3/CON?.mp3").parts[-1] != "CON?.mp3"


def test_smart_audio_requires_format_directories_and_durations() -> None:
    files = (
        remote("mp3", "mp3/voice.mp3"),
        remote("wav", "wav/voice.wav", duration=10.3),
        remote("unknown", "audio/voice.ogg", duration=None),
    )
    selected = smart_audio_selection(files)
    assert selected == {"mp3", "unknown"}


def test_download_manifest_records_unselected_files(tmp_path: Path, monkeypatch) -> None:
    plan = WorkPlan("1528633", "RJ01528633", "Title", "Circle", "", "https://example.test",
                    (remote("a", "mp3/a.mp3"), remote("b", "text.txt")))

    class FakeProcess:
        returncode = 0

        def poll(self):
            return 0

        def communicate(self):
            return ("\n200", "")

    def fake_popen(command, **_kwargs):
        output = Path(command[command.index("--output") + 1])
        output.write_bytes(b"data")
        return FakeProcess()

    monkeypatch.setattr("asmr_lrc.downloader._curl_executable", lambda _config: "curl.exe")
    monkeypatch.setattr("asmr_lrc.downloader.subprocess.Popen", fake_popen)
    root = download_plan(plan, {"a"}, tmp_path, DownloadConfig())
    manifest = json.loads((root / "download.manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"]["a"]["completed"] is True
    assert manifest["files"]["b"]["selected"] is False
    assert "media" not in (root / "download.manifest.json").read_text(encoding="utf-8")


def test_worker_download_plan_returns_public_metadata(monkeypatch, capsys) -> None:
    plan = WorkPlan("1528633", "RJ01528633", "Title", "Circle", "", "https://example.test",
                    (remote("a", "mp3/a.mp3"),))
    monkeypatch.setattr("asmr_lrc.session.fetch_work_plan", lambda _rj, _config: plan)
    assert dispatch({"protocol": 1, "command": "download_plan", "rj": "RJ01528633"}) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["event"] == "download_metadata"
    assert output["plan"]["files"][0]["id"] == "a"
    assert "mediaDownloadUrl" not in json.dumps(output)


def test_worker_download_run_maps_structured_events(monkeypatch, tmp_path: Path, capsys) -> None:
    plan = WorkPlan("1528633", "RJ01528633", "Title", "Circle", "", "https://example.test",
                    (remote("a", "mp3/a.mp3"),))
    observed: dict[str, object] = {}

    def fake_download(plan_arg, selected, root, config, *, token, callback):
        observed.update({"plan": plan_arg, "selected": selected, "root": root})
        callback({"event": "progress", "id": "a", "path": "mp3/a.mp3", "size": 4, "total": 4})
        callback(
            {"event": "complete", "root": str(root), "source_id": plan_arg.source_id, "total": 4}
        )
        return root

    monkeypatch.setattr("asmr_lrc.session.fetch_work_plan", lambda _rj, _config: plan)
    monkeypatch.setattr("asmr_lrc.session.download_plan", fake_download)
    dispatch({
        "protocol": 1,
        "command": "download_run",
        "plan": plan.public_dict(),
        "selected_ids": ["a"],
        "output_root": str(tmp_path),
    })
    output = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [item["event"] for item in output] == ["download_progress", "download_complete"]
    assert observed["selected"] == {"a"}


def test_download_config_rejects_unknown_endpoint() -> None:
    with pytest.raises(ValueError):
        DownloadConfig(endpoint="file:///tmp")
