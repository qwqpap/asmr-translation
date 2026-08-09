import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from asmr_lrc.cache import atomic_write_json, cache_directory, source_identity
from asmr_lrc.gui_worker import dispatch
from asmr_lrc.models import (
    FilteredTranscript,
    Segment,
    Translation,
    TranslationItem,
)


def make_cached_audio(tmp_path: Path) -> tuple[Path, Path]:
    audio = tmp_path / "中文 音频.wav"
    audio.write_bytes(b"audio")
    cache = tmp_path / "cache"
    source = source_identity(audio)
    directory = cache_directory(cache, source)
    segments = (
        Segment("s1", 0, 1, "こんにちは"),
        Segment("s2", 2, 3, "また明日"),
    )
    filtered = FilteredTranscript(source, {}, segments, ())
    translation = Translation(
        source=source,
        model="model",
        created_at=datetime.now(UTC).isoformat(),
        batches=(),
        items=(TranslationItem("s1", "你好"), TranslationItem("s2", "明天见")),
        profile_id="profile",
        stage="final",
        draft_model="model",
        review_model="model",
    )
    atomic_write_json(directory / "transcript.filtered.json", filtered.to_dict())
    atomic_write_json(directory / "translation.zh-CN.json", translation.to_dict())
    audio.with_suffix(".lrc").write_text(
        "[00:00.00]你好\n[00:02.00]明天见\n", encoding="utf-8"
    )
    return audio, cache


def events(captured: str) -> list[dict]:
    return [json.loads(line) for line in captured.splitlines() if line.strip()]


def test_worker_loads_bilingual_cues(tmp_path: Path, capsys) -> None:
    audio, cache = make_cached_audio(tmp_path)

    exit_code = dispatch(
        {"protocol": 1, "command": "load_cues", "audio": str(audio), "cache_root": str(cache)}
    )

    assert exit_code == 0
    result = events(capsys.readouterr().out)[0]
    assert result["event"] == "cues"
    assert result["cues"][0]["source"] == "こんにちは"
    assert result["cues"][0]["text"] == "你好"


def test_worker_saves_edits_with_one_time_backup(tmp_path: Path, capsys) -> None:
    audio, cache = make_cached_audio(tmp_path)
    request = {
        "protocol": 1,
        "command": "save_edits",
        "audio": str(audio),
        "cache_root": str(cache),
        "edits": [{"id": "s1", "text": "您好"}],
    }

    dispatch(request)
    first = events(capsys.readouterr().out)[0]
    backup = Path(first["backup"])
    assert backup.read_text(encoding="utf-8") == "[00:00.00]你好\n[00:02.00]明天见\n"
    assert audio.with_suffix(".lrc").read_text(encoding="utf-8") == (
        "[00:00.00]您好\n[00:02.00]明天见\n"
    )

    dispatch({**request, "edits": [{"id": "s1", "text": "你好呀"}]})
    capsys.readouterr()
    assert backup.read_text(encoding="utf-8") == "[00:00.00]你好\n[00:02.00]明天见\n"


def test_worker_prepares_pcm_proxy_and_reuses_it(tmp_path: Path, monkeypatch, capsys) -> None:
    audio = tmp_path / "voice.opus"
    audio.write_bytes(b"opus")
    cache = tmp_path / "cache"
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b"wave")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("asmr_lrc.gui_worker.subprocess.run", fake_run)
    request = {
        "protocol": 1,
        "command": "prepare_playback",
        "audio": str(audio),
        "cache_root": str(cache),
    }

    dispatch(request)
    first = events(capsys.readouterr().out)[0]
    stale = Path(first["path"]).with_name(f".{Path(first['path']).name}.123.tmp.wav")
    stale.write_bytes(b"partial")
    dispatch(request)
    second = events(capsys.readouterr().out)[0]

    assert Path(first["path"]).read_bytes() == b"wave"
    assert first["path"] == second["path"]
    assert len(calls) == 1
    assert not stale.exists()


def test_worker_jsonl_protocol_round_trip_in_subprocess(tmp_path: Path) -> None:
    audio, cache = make_cached_audio(tmp_path)
    request = {
        "protocol": 1,
        "command": "load_cues",
        "audio": str(audio),
        "cache_root": str(cache),
    }

    result = subprocess.run(
        [sys.executable, "-m", "asmr_lrc.gui_worker"],
        input=json.dumps(request, ensure_ascii=False) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
        timeout=15,
    )

    assert result.returncode == 0
    output = events(result.stdout)
    assert output[0]["protocol"] == 1
    assert output[0]["event"] == "cues"
    assert output[0]["cues"][1]["text"] == "明天见"


def test_worker_exits_cleanly_while_control_pipe_remains_open(tmp_path: Path) -> None:
    audio, cache = make_cached_audio(tmp_path)
    request = {
        "protocol": 1,
        "command": "load_cues",
        "audio": str(audio),
        "cache_root": str(cache),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "asmr_lrc.gui_worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    try:
        process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        process.stdin.flush()
        returncode = process.wait(timeout=15)
        stdout = process.stdout.read()
        stderr = process.stderr.read()
    finally:
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()

    assert returncode == 0
    assert events(stdout)[0]["event"] == "cues"
    assert "Fatal Python error" not in stderr


def test_worker_probe_external_only_does_not_require_ollama(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    observed: dict[str, object] = {}

    def fake_probe(ollama_url, ollama_model, **kwargs):
        observed.update(
            {"ollama_url": ollama_url, "ollama_model": ollama_model, **kwargs}
        )
        return {"checks": [], "ok": True}

    monkeypatch.setattr("asmr_lrc.gui_worker.probe_environment", fake_probe)
    dispatch(
        {
            "protocol": 1,
            "command": "probe",
            "config": {
                "cache_root": str(tmp_path / "cache"),
                "draft_provider": {
                    "kind": "openai",
                    "base_url": "https://example.test/v1",
                    "model": "remote-model",
                    "api_key": "probe-secret",
                },
                "review_provider": "same",
                "ffmpeg_path": "C:/tools/ffmpeg.exe",
            },
        }
    )

    output = capsys.readouterr().out
    assert observed["ollama_url"] is None
    assert observed["ollama_model"] is None
    assert observed["ffmpeg_path"] == "C:/tools/ffmpeg.exe"
    assert "probe-secret" not in output
