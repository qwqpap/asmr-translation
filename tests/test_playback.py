"""Playback engine tests.

These exercise the parts that must be correct without an audio device: header
parsing, sample normalization, and the WSOLA stretcher.  The pitch test is the
important one -- it is what distinguishes real time-scale modification from a
naive resample, which is the failure mode this engine exists to avoid.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path

import numpy as np
import pytest

from asmr_lrc.playback import (
    MAX_RATE,
    MIN_RATE,
    PlaybackError,
    _resample_linear,
    _Stretcher,
    _to_float32,
    open_pcm_wav,
)

SAMPLE_RATE = 48_000


def _write_wav(path: Path, samples: np.ndarray, *, channels: int = 1) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(samples.astype("<i2").tobytes())
    return path


def _sine(frequency: float, seconds: float) -> np.ndarray:
    time_axis = np.arange(int(SAMPLE_RATE * seconds), dtype=np.float64) / SAMPLE_RATE
    return (np.sin(2 * np.pi * frequency * time_axis) * 20_000).astype(np.int16)


def _dominant_frequency(block: np.ndarray) -> float:
    mono = block.mean(axis=1) if block.ndim > 1 else block
    windowed = mono * np.hanning(len(mono))
    spectrum = np.abs(np.fft.rfft(windowed))
    return float(np.fft.rfftfreq(len(mono), 1 / SAMPLE_RATE)[int(np.argmax(spectrum))])


def _reader(samples: np.ndarray, channels: int):
    frames = samples.reshape(-1, channels)

    def read(start: int, count: int) -> np.ndarray:
        begin = max(0, min(start, frames.shape[0]))
        end = max(begin, min(begin + count, frames.shape[0]))
        return _to_float32(frames[begin:end], "<i2")

    return read


def test_open_pcm_wav_reports_geometry(tmp_path: Path) -> None:
    path = _write_wav(tmp_path / "mono.wav", _sine(440, 0.5))
    source = open_pcm_wav(path)
    assert source.sample_rate == SAMPLE_RATE
    assert source.channels == 1
    assert source.frames == SAMPLE_RATE // 2
    assert source.duration == pytest.approx(0.5, abs=1e-3)
    assert source.dtype == "<i2"


def test_open_pcm_wav_handles_stereo(tmp_path: Path) -> None:
    mono = _sine(440, 0.25)
    stereo = np.repeat(mono, 2)
    path = _write_wav(tmp_path / "stereo.wav", stereo, channels=2)
    source = open_pcm_wav(path)
    assert source.channels == 2
    assert source.frames == len(mono)


def test_open_pcm_wav_reads_extensible_subformat(tmp_path: Path) -> None:
    """FFmpeg emits WAVE_FORMAT_EXTENSIBLE for some channel layouts."""
    payload = _sine(440, 0.1).tobytes()
    fmt = struct.pack(
        "<HHIIHH", 0xFFFE, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16
    ) + struct.pack("<HHI", 22, 16, 0x4) + struct.pack("<H", 0x0001) + b"\x00" * 14
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(payload)) + payload
    path = tmp_path / "extensible.wav"
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    source = open_pcm_wav(path)
    assert source.dtype == "<i2"
    assert source.frames == len(payload) // 2


def test_open_pcm_wav_rejects_non_wave(tmp_path: Path) -> None:
    path = tmp_path / "bad.wav"
    path.write_bytes(b"OggS" + b"\x00" * 64)
    with pytest.raises(PlaybackError):
        open_pcm_wav(path)


def test_open_pcm_wav_rejects_unsupported_depth(tmp_path: Path) -> None:
    fmt = struct.pack("<HHIIHH", 1, 1, SAMPLE_RATE, SAMPLE_RATE * 3, 3, 24)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", 6) + b"\x00" * 6
    path = tmp_path / "24bit.wav"
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    with pytest.raises(PlaybackError, match="24 位"):
        open_pcm_wav(path)


def test_open_pcm_wav_trusts_file_size_over_placeholder_length(tmp_path: Path) -> None:
    """Streamed WAVs sometimes declare 0xFFFFFFFF bytes of data."""
    payload = _sine(440, 0.05).tobytes()
    fmt = struct.pack("<HHIIHH", 1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", 0xFFFFFFFF) + payload
    path = tmp_path / "streamed.wav"
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    source = open_pcm_wav(path)
    assert source.frames == len(payload) // 2


def test_to_float32_normalizes_full_scale() -> None:
    block = np.array([[-32768], [0], [32767]], dtype="<i2")
    converted = _to_float32(block, "<i2")
    assert converted.dtype == np.float32
    assert converted[0, 0] == pytest.approx(-1.0)
    assert converted[1, 0] == pytest.approx(0.0)
    assert converted[2, 0] == pytest.approx(1.0, abs=1e-4)


def test_stretcher_preserves_pitch_when_slowing_down() -> None:
    """Time-stretching must not transpose; a resample would shift 440 Hz down."""
    samples = _sine(440, 3.0)
    stretcher = _Stretcher(SAMPLE_RATE, 1)
    output = stretcher.process(_reader(samples, 1), SAMPLE_RATE, 0.75, len(samples))
    assert _dominant_frequency(output) == pytest.approx(440, abs=12)


def test_stretcher_preserves_pitch_when_speeding_up() -> None:
    samples = _sine(440, 3.0)
    stretcher = _Stretcher(SAMPLE_RATE, 1)
    output = stretcher.process(_reader(samples, 1), SAMPLE_RATE, 2.0, len(samples))
    assert _dominant_frequency(output) == pytest.approx(440, abs=12)


@pytest.mark.parametrize("rate", [MIN_RATE, 1.25, 1.5, MAX_RATE])
def test_stretcher_advances_source_by_rate(rate: float) -> None:
    """One second of output must consume ``rate`` seconds of source."""
    samples = _sine(220, 6.0)
    stretcher = _Stretcher(SAMPLE_RATE, 1)
    stretcher.process(_reader(samples, 1), SAMPLE_RATE, rate, len(samples))
    consumed = stretcher.source_position / SAMPLE_RATE
    assert consumed == pytest.approx(rate, rel=0.05)


def test_stretcher_output_length_matches_request() -> None:
    samples = _sine(330, 2.0)
    stretcher = _Stretcher(SAMPLE_RATE, 1)
    for _ in range(4):
        block = stretcher.process(_reader(samples, 1), 1024, 1.5, len(samples))
        assert block.shape == (1024, 1)


def test_stretcher_handles_stereo_without_channel_bleed() -> None:
    """A silent right channel must stay silent after stretching."""
    left = _sine(440, 1.5)
    interleaved = np.empty(left.size * 2, dtype=np.int16)
    interleaved[0::2] = left
    interleaved[1::2] = 0
    stretcher = _Stretcher(SAMPLE_RATE, 2)
    output = stretcher.process(_reader(interleaved, 2), SAMPLE_RATE, 1.5, left.size)
    assert np.abs(output[:, 0]).max() > 0.2
    assert np.abs(output[:, 1]).max() == pytest.approx(0.0, abs=1e-6)


def test_stretcher_pads_and_stops_at_end_of_source() -> None:
    samples = _sine(440, 0.2)
    stretcher = _Stretcher(SAMPLE_RATE, 1)
    block = stretcher.process(_reader(samples, 1), SAMPLE_RATE, 1.5, len(samples))
    assert block.shape[0] == SAMPLE_RATE
    assert stretcher.source_position >= len(samples) - SAMPLE_RATE * 0.05


def test_stretcher_reset_moves_source_position() -> None:
    samples = _sine(440, 2.0)
    stretcher = _Stretcher(SAMPLE_RATE, 1)
    stretcher.process(_reader(samples, 1), 4096, 1.5, len(samples))
    stretcher.reset(SAMPLE_RATE)
    assert stretcher.source_position == SAMPLE_RATE


def test_resample_linear_changes_length_but_keeps_shape() -> None:
    block = np.linspace(-1.0, 1.0, 480, dtype=np.float32).reshape(-1, 2)
    resampled = _resample_linear(block, 320)
    assert resampled.shape == (320, 2)
    assert resampled.dtype == np.float32
    assert resampled[0, 0] == pytest.approx(block[0, 0])


def test_resample_linear_handles_empty_input() -> None:
    empty = np.zeros((0, 2), dtype=np.float32)
    assert _resample_linear(empty, 128).shape == (128, 2)
