from __future__ import annotations

import argparse
import subprocess
import threading
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path

from .cache import atomic_write_json, source_identity
from .environment import configure_cuda_runtime
from .models import Segment, Transcript, WordTiming


class GpuMemoryMonitor:
    def __init__(self) -> None:
        self.peak_mib: int | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.is_set():
            try:
                result = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=memory.used",
                        "--format=csv,noheader,nounits",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if result.returncode == 0:
                    values = [
                        int(line.strip()) for line in result.stdout.splitlines() if line.strip()
                    ]
                    if values:
                        sample = max(values)
                        self.peak_mib = (
                            sample if self.peak_mib is None else max(self.peak_mib, sample)
                        )
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
            self._stop.wait(0.5)

    def __enter__(self) -> GpuMemoryMonitor:
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2)


def transcribe(args: argparse.Namespace) -> Transcript:
    configure_cuda_runtime()
    from faster_whisper import WhisperModel

    source = Path(args.audio)
    identity = source_identity(source)
    started = time.perf_counter()
    with GpuMemoryMonitor() as monitor:
        model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
        generated, info = model.transcribe(
            str(source),
            language="ja",
            beam_size=5,
            best_of=5,
            temperature=0.0,
            condition_on_previous_text=True,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={
                "threshold": 0.25,
                "min_speech_duration_ms": 150,
                "min_silence_duration_ms": 1000,
                "speech_pad_ms": 500,
            },
        )
        segments: list[Segment] = []
        for index, item in enumerate(generated):
            words = tuple(
                WordTiming(
                    start=float(word.start),
                    end=float(word.end),
                    word=str(word.word),
                    probability=(None if word.probability is None else float(word.probability)),
                )
                for word in (item.words or [])
                if word.start is not None and word.end is not None
            )
            segments.append(
                Segment(
                    id=f"s{index + 1:06d}",
                    start=float(item.start),
                    end=float(item.end),
                    text=str(item.text).strip(),
                    avg_logprob=(None if item.avg_logprob is None else float(item.avg_logprob)),
                    no_speech_prob=(
                        None if item.no_speech_prob is None else float(item.no_speech_prob)
                    ),
                    words=words,
                )
            )
    elapsed = time.perf_counter() - started
    duration = None if getattr(info, "duration", None) is None else float(info.duration)
    return Transcript(
        source=identity,
        model=args.model,
        device=args.device,
        compute_type=args.compute_type,
        language="ja",
        created_at=datetime.now(UTC).isoformat(),
        elapsed_seconds=elapsed,
        duration_seconds=duration,
        peak_gpu_memory_mib=monitor.peak_mib,
        segments=tuple(segments),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--compute-type", required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = transcribe(args)
        atomic_write_json(Path(args.output), result.to_dict())
        return 0
    except Exception:  # the parent records this debug traceback separately
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
