"""Sample-accurate PCM playback with pitch-preserving speed control.

The Win32 GUI used ``MFMediaEngine``, which is Windows-only.  ``QMediaPlayer``
would be the obvious portable swap, but on Linux it delegates to GStreamer, whose
``position()`` granularity and post-seek behaviour are too coarse for the 50 ms
lyric refresh this app needs.

So playback is built directly on PortAudio instead, which is viable only because
``prepare_playback`` already renders every input format to PCM WAV: this module
needs no codec, just a way to push samples and know exactly which source frame
the listener is hearing.

Two things make that exact:

* The source is memory-mapped, so the clock is derived from a frame index rather
  than from a decoder's estimate.
* Each callback records the DAC time of its block, so ``position`` interpolates
  against the audio device's own clock instead of wall time.

Speed changes run through WSOLA, which resamples the *time* axis while leaving
pitch alone -- a plain resample would make every whisper sound chipmunked.  At
rate 1.0 the stretcher is bypassed entirely, so the default path is a straight
memory copy.
"""

from __future__ import annotations

import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

MIN_RATE = 0.75
MAX_RATE = 2.0
_WSOLA_FRAME_SECONDS = 0.030
_WSOLA_SEARCH_SECONDS = 0.010
_BLOCK_FRAMES = 1024

_WAVE_FORMAT_PCM = 0x0001
_WAVE_FORMAT_FLOAT = 0x0003
_WAVE_FORMAT_EXTENSIBLE = 0xFFFE


class PlaybackError(RuntimeError):
    """The audio device or the PCM proxy could not be used."""


@dataclass(frozen=True, slots=True)
class PcmSource:
    """A memory-mappable PCM WAV file."""

    path: Path
    sample_rate: int
    channels: int
    frames: int
    data_offset: int
    dtype: str

    @property
    def duration(self) -> float:
        return 0.0 if self.sample_rate <= 0 else self.frames / self.sample_rate


def open_pcm_wav(path: Path) -> PcmSource:
    """Parse a WAV header by walking RIFF chunks.

    ``wave`` from the standard library would do, but it does not expose the data
    chunk offset, and that offset is what lets the samples be memory-mapped
    instead of read into RAM -- proxies for long ASMR tracks reach hundreds of MB.
    """
    with path.open("rb") as stream:
        header = stream.read(12)
        if len(header) < 12 or header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise PlaybackError(f"不是 RIFF/WAVE 文件: {path}")
        sample_format: int | None = None
        channels = 0
        sample_rate = 0
        bits = 0
        data_offset = 0
        data_size = 0
        while True:
            chunk_header = stream.read(8)
            if len(chunk_header) < 8:
                break
            chunk_id, chunk_size = struct.unpack("<4sI", chunk_header)
            body_start = stream.tell()
            if chunk_id == b"fmt ":
                body = stream.read(min(chunk_size, 40))
                if len(body) < 16:
                    raise PlaybackError(f"WAV fmt 块不完整: {path}")
                (
                    sample_format,
                    channels,
                    sample_rate,
                    _byte_rate,
                    _block_align,
                    bits,
                ) = struct.unpack("<HHIIHH", body[:16])
                if sample_format == _WAVE_FORMAT_EXTENSIBLE and len(body) >= 40:
                    # The real format lives in the sub-format GUID's first field.
                    sample_format = struct.unpack("<H", body[24:26])[0]
            elif chunk_id == b"data":
                data_offset = body_start
                data_size = chunk_size
                # Trailing chunks after data are irrelevant for playback.
                break
            stream.seek(body_start + chunk_size + (chunk_size & 1))
        if sample_format is None or not data_offset:
            raise PlaybackError(f"WAV 缺少 fmt 或 data 块: {path}")
        if channels < 1 or sample_rate < 1:
            raise PlaybackError(f"WAV 声道数或采样率无效: {path}")
        actual = path.stat().st_size - data_offset
        if data_size <= 0 or data_size > actual:
            # Streamed WAVs sometimes carry a placeholder size; trust the file.
            data_size = actual
    dtype = _numpy_dtype(sample_format, bits, path)
    frame_bytes = channels * np.dtype(dtype).itemsize
    if frame_bytes <= 0:
        raise PlaybackError(f"无法确定 WAV 帧大小: {path}")
    return PcmSource(
        path=path,
        sample_rate=sample_rate,
        channels=channels,
        frames=data_size // frame_bytes,
        data_offset=data_offset,
        dtype=dtype,
    )


def _numpy_dtype(sample_format: int, bits: int, path: Path) -> str:
    if sample_format == _WAVE_FORMAT_PCM:
        if bits == 16:
            return "<i2"
        if bits == 32:
            return "<i4"
        if bits == 8:
            return "u1"
        raise PlaybackError(
            f"暂不支持 {bits} 位整数 PCM（{path.name}）。"
            "请删除该播放代理，让 FFmpeg 重新生成 16 位 PCM。"
        )
    if sample_format == _WAVE_FORMAT_FLOAT and bits == 32:
        return "<f4"
    raise PlaybackError(
        f"暂不支持的 WAV 采样格式 0x{sample_format:04X}/{bits} 位（{path.name}）。"
    )


def _to_float32(block: np.ndarray, dtype: str) -> np.ndarray:
    """Normalize any supported PCM layout to float32 in [-1, 1]."""
    if dtype == "<f4":
        return block.astype(np.float32, copy=False)
    if dtype == "u1":
        return (block.astype(np.float32) - 128.0) / 128.0
    info = np.iinfo(np.dtype(dtype))
    return block.astype(np.float32) / float(-info.min)


class _Stretcher:
    """WSOLA time-scale modification.

    Each output hop is built by searching a small neighbourhood of the source for
    the frame that best continues the previous output, then cross-fading.  That
    search is what preserves pitch: periods are repeated or dropped whole rather
    than stretched.
    """

    __slots__ = (
        "_channels",
        "_frame",
        "_hop",
        "_search",
        "_window",
        "_overlap",
        "_pending",
        "_position",
    )

    def __init__(self, sample_rate: int, channels: int) -> None:
        self._channels = channels
        self._frame = max(64, int(sample_rate * _WSOLA_FRAME_SECONDS))
        self._hop = self._frame // 2
        self._search = max(1, int(sample_rate * _WSOLA_SEARCH_SECONDS))
        ramp = np.linspace(0.0, 1.0, self._hop, endpoint=False, dtype=np.float32)
        self._window = ramp.reshape(-1, 1)
        self._overlap = np.zeros((self._hop, channels), dtype=np.float32)
        self._pending = np.zeros((0, channels), dtype=np.float32)
        self._position = 0.0

    @property
    def source_position(self) -> float:
        """Fractional source frame index the next output hop will read from."""
        return self._position

    def reset(self, position: float) -> None:
        self._overlap[:] = 0.0
        self._pending = np.zeros((0, self._channels), dtype=np.float32)
        self._position = float(position)

    def _best_offset(self, reader: Callable[[int, int], np.ndarray], base: int) -> int:
        """Find the shift whose head best matches the tail we already emitted."""
        if not self._overlap.any():
            return 0
        span = self._search * 2 + self._hop
        start = max(0, base - self._search)
        candidates = reader(start, span)
        if candidates.shape[0] < self._hop:
            return 0
        target = self._overlap.mean(axis=1)
        pool = candidates.mean(axis=1)
        # Normalized cross-correlation keeps the match from chasing loud frames,
        # which matters for whisper-quiet ASMR material.
        windows = np.lib.stride_tricks.sliding_window_view(pool, self._hop)
        limit = windows.shape[0]
        scores = windows @ target
        energy = np.sqrt(np.einsum("ij,ij->i", windows, windows)) + 1e-9
        best = int(np.argmax(scores / energy))
        return max(0, start + min(best, limit - 1)) - base

    def process(
        self,
        reader: Callable[[int, int], np.ndarray],
        wanted: int,
        rate: float,
        total_frames: int,
    ) -> np.ndarray:
        """Produce ``wanted`` output frames, advancing the source by ``rate``."""
        hop_in = self._hop * rate
        while self._pending.shape[0] < wanted:
            base = int(self._position)
            if base >= total_frames:
                break
            offset = self._best_offset(reader, base)
            chunk = reader(max(0, base + offset), self._frame)
            if chunk.shape[0] < self._frame:
                padding = np.zeros(
                    (self._frame - chunk.shape[0], self._channels), dtype=np.float32
                )
                chunk = np.concatenate((chunk, padding), axis=0)
            head = chunk[: self._hop]
            tail = chunk[self._hop : self._hop * 2]
            blended = self._overlap * (1.0 - self._window) + head * self._window
            self._pending = np.concatenate((self._pending, blended), axis=0)
            self._overlap = tail.copy()
            self._position += hop_in
        take = min(wanted, self._pending.shape[0])
        output = self._pending[:take]
        self._pending = self._pending[take:]
        if take < wanted:
            padding = np.zeros((wanted - take, self._channels), dtype=np.float32)
            output = np.concatenate((output, padding), axis=0)
        return output


class PcmPlayer:
    """Plays a PCM WAV proxy with a sample-accurate position clock."""

    def __init__(self, *, on_finished: Callable[[], None] | None = None) -> None:
        self._lock = threading.RLock()
        self._source: PcmSource | None = None
        self._samples: np.memmap | None = None
        self._stream: Any = None
        self._stretcher: _Stretcher | None = None
        self._rate = 1.0
        self._volume = 1.0
        self._playing = False
        self._finished = False
        self._on_finished = on_finished
        self._device_rate: int | None = None
        self._resample_ratio = 1.0
        self._direct_position = 0.0
        # Written by the audio thread, read by the UI thread.
        self._block_dac_time = 0.0
        self._block_start_position = 0.0
        self._block_end_position = 0.0

    # --- lifecycle ---------------------------------------------------------

    @staticmethod
    def _sounddevice() -> Any:
        try:
            import sounddevice
        except (ImportError, OSError) as exc:
            raise PlaybackError(
                "无法加载音频后端 sounddevice/PortAudio："
                f"{exc}。Linux 上通常需要安装 libportaudio2。"
            ) from exc
        return sounddevice

    def load(self, path: Path) -> PcmSource:
        """Open a PCM proxy and arm the output stream, stopped at position 0."""
        source = open_pcm_wav(path)
        samples = np.memmap(
            path,
            dtype=source.dtype,
            mode="r",
            offset=source.data_offset,
            shape=(source.frames, source.channels),
        )
        sounddevice = self._sounddevice()
        with self._lock:
            self._teardown_stream()
            self._source = source
            self._samples = samples
            self._stretcher = _Stretcher(source.sample_rate, source.channels)
            self._direct_position = 0.0
            self._finished = False
            self._playing = False
            self._block_dac_time = 0.0
            self._block_start_position = 0.0
            self._block_end_position = 0.0
            device_rate, ratio = self._negotiate_rate(sounddevice, source)
            self._device_rate = device_rate
            self._resample_ratio = ratio
            try:
                self._stream = sounddevice.OutputStream(
                    samplerate=device_rate,
                    channels=source.channels,
                    dtype="float32",
                    blocksize=_BLOCK_FRAMES,
                    # Low latency keeps the gap between the reported position and
                    # what the listener hears inside the lyric refresh interval.
                    latency="low",
                    callback=self._callback,
                    finished_callback=None,
                )
                self._stream.start()
            except Exception as exc:
                self._stream = None
                raise PlaybackError(f"无法打开音频输出设备: {exc}") from exc
        return source

    def _negotiate_rate(self, sounddevice: Any, source: PcmSource) -> tuple[int, float]:
        """Prefer the file's own rate; fall back to the device's with resampling."""
        try:
            sounddevice.check_output_settings(
                samplerate=source.sample_rate, channels=source.channels, dtype="float32"
            )
            return source.sample_rate, 1.0
        except Exception:
            pass
        try:
            default = sounddevice.query_devices(kind="output")
            device_rate = int(default["default_samplerate"])
        except Exception as exc:
            raise PlaybackError(f"没有可用的音频输出设备: {exc}") from exc
        return device_rate, source.sample_rate / device_rate

    def close(self) -> None:
        with self._lock:
            self._teardown_stream()
            self._samples = None
            self._source = None
            self._stretcher = None

    def _teardown_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:
            # A device disappearing mid-teardown must not mask the caller's work.
            pass

    # --- transport ---------------------------------------------------------

    def play(self) -> None:
        with self._lock:
            if self._source is None:
                return
            if self._finished:
                self._seek_locked(0.0)
            self._playing = True

    def pause(self) -> None:
        with self._lock:
            self._playing = False

    def toggle(self) -> bool:
        with self._lock:
            if self._playing:
                self._playing = False
            else:
                if self._finished:
                    self._seek_locked(0.0)
                self._playing = self._source is not None
            return self._playing

    def stop(self) -> None:
        with self._lock:
            self._playing = False
            self._seek_locked(0.0)

    def seek(self, seconds: float) -> None:
        with self._lock:
            self._seek_locked(seconds)

    def _seek_locked(self, seconds: float) -> None:
        if self._source is None:
            return
        frame = max(0.0, min(seconds, self._source.duration)) * self._source.sample_rate
        self._direct_position = frame
        if self._stretcher is not None:
            self._stretcher.reset(frame)
        self._finished = False
        self._block_start_position = frame
        self._block_end_position = frame
        self._block_dac_time = 0.0

    def set_rate(self, rate: float) -> float:
        with self._lock:
            clamped = max(MIN_RATE, min(MAX_RATE, float(rate)))
            if clamped == self._rate:
                return clamped
            # Hand the stretcher the position the listener is actually at, so a
            # speed change does not jump the timeline.
            position = self._raw_position_locked()
            self._rate = clamped
            if self._source is not None:
                frame = position * self._source.sample_rate
                self._direct_position = frame
                if self._stretcher is not None:
                    self._stretcher.reset(frame)
                self._block_start_position = frame
                self._block_end_position = frame
                self._block_dac_time = 0.0
            return clamped

    def set_volume(self, volume: float) -> float:
        with self._lock:
            self._volume = max(0.0, min(1.0, float(volume)))
            return self._volume

    # --- state -------------------------------------------------------------

    @property
    def rate(self) -> float:
        return self._rate

    @property
    def volume(self) -> float:
        return self._volume

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def is_loaded(self) -> bool:
        return self._source is not None

    @property
    def duration(self) -> float:
        source = self._source
        return 0.0 if source is None else source.duration

    def _raw_position_locked(self) -> float:
        source = self._source
        if source is None:
            return 0.0
        if not self._playing or self._block_dac_time <= 0.0 or self._stream is None:
            return self._block_start_position / source.sample_rate
        try:
            now = float(self._stream.time)
        except Exception:
            now = 0.0
        elapsed = now - self._block_dac_time
        span = self._block_end_position - self._block_start_position
        if span <= 0.0 or elapsed <= 0.0:
            frame = self._block_start_position
        else:
            # Interpolate against the device clock, clamped to this block so a
            # stalled callback can never let the lyric cursor run ahead.
            block_seconds = span / (source.sample_rate * max(self._rate, 1e-6))
            progress = min(1.0, elapsed / max(block_seconds, 1e-9))
            frame = self._block_start_position + span * progress
        return max(0.0, min(frame, float(source.frames))) / source.sample_rate

    @property
    def position(self) -> float:
        """Current source-timeline position in seconds."""
        with self._lock:
            return self._raw_position_locked()

    # --- audio thread ------------------------------------------------------

    def _read(self, start: int, count: int) -> np.ndarray:
        samples = self._samples
        source = self._source
        if samples is None or source is None or count <= 0:
            return np.zeros((0, 1), dtype=np.float32)
        begin = max(0, min(start, source.frames))
        end = max(begin, min(begin + count, source.frames))
        if end <= begin:
            return np.zeros((0, source.channels), dtype=np.float32)
        return _to_float32(np.asarray(samples[begin:end]), source.dtype)

    def _callback(self, outdata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        del status  # Underruns are audible; there is nothing useful to do here.
        with self._lock:
            source = self._source
            if source is None or not self._playing:
                outdata[:] = 0.0
                return
            wanted = int(round(frames * self._resample_ratio))
            start_position = self._effective_position()
            block = self._render(wanted, source)
            end_position = self._effective_position()
            if self._resample_ratio != 1.0:
                block = _resample_linear(block, frames)
            if block.shape[0] < frames:
                padding = np.zeros((frames - block.shape[0], source.channels), np.float32)
                block = np.concatenate((block, padding), axis=0)
            outdata[:] = block[:frames] * self._volume
            self._block_start_position = start_position
            self._block_end_position = end_position
            try:
                self._block_dac_time = float(time_info.outputBufferDacTime)
            except Exception:
                self._block_dac_time = 0.0
            if end_position >= source.frames:
                self._playing = False
                self._finished = True
                callback = self._on_finished
                if callback is not None:
                    # Fired on the audio thread; Qt consumers marshal via signals.
                    callback()

    def _effective_position(self) -> float:
        if self._rate == 1.0 or self._stretcher is None:
            return self._direct_position
        return self._stretcher.source_position

    def _render(self, wanted: int, source: PcmSource) -> np.ndarray:
        if self._rate == 1.0:
            start = int(self._direct_position)
            block = self._read(start, wanted)
            self._direct_position = min(float(source.frames), start + float(block.shape[0]))
            return block
        assert self._stretcher is not None
        return self._stretcher.process(self._read, wanted, self._rate, source.frames)


def _resample_linear(block: np.ndarray, frames: int) -> np.ndarray:
    """Linear resample, used only when the device rejects the file's rate."""
    if block.shape[0] == 0 or frames <= 0:
        return np.zeros((max(frames, 0), block.shape[1] if block.ndim > 1 else 1), np.float32)
    source_index = np.linspace(0.0, block.shape[0] - 1, frames, dtype=np.float32)
    base = np.floor(source_index).astype(np.int64)
    upper = np.minimum(base + 1, block.shape[0] - 1)
    weight = (source_index - base).reshape(-1, 1)
    return (block[base] * (1.0 - weight) + block[upper] * weight).astype(np.float32)
