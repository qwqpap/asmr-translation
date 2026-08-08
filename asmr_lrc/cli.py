from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import AppConfig
from .environment import probe_environment
from .pipeline import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asmr-lrc",
        description="本地批量识别日语 ASMR 并生成简体中文 LRC",
    )
    parser.add_argument("folder", nargs="?", type=Path, help="递归扫描的音频文件夹")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--transcribe-only", action="store_true", help="只转写并写入缓存")
    mode.add_argument("--translate-only", action="store_true", help="只使用已有转写缓存翻译")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已有同名 LRC")
    parser.add_argument("--dry-run", action="store_true", help="只显示处理计划，不写任何文件")
    parser.add_argument("--probe", action="store_true", help="探测本地运行环境后退出")
    parser.add_argument("--asr-model", default="large-v3", help="faster-whisper 模型")
    parser.add_argument(
        "--fallback-asr-model",
        metavar="MODEL",
        help="ASR 失败后仅额外尝试一次的显式降级模型（例如 medium）",
    )
    parser.add_argument("--device", default="cuda", choices=("cuda", "cpu"))
    parser.add_argument("--compute-type", default="int8_float16")
    parser.add_argument("--ollama-model", default="qwen3.5-9b-abliterated:latest")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--translation-retries", type=int, default=2)
    parser.add_argument("--cache-dir", type=Path, default=Path.cwd() / ".cache")
    parser.add_argument(
        "--keep-ollama",
        action="store_true",
        help="处理结束后让 Ollama 模型保持加载（下次 ASR 前需手动停止）",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.folder is None and not args.probe:
        parser.error("必须提供音频文件夹，或使用 --probe")
    try:
        config = AppConfig(
            cache_root=args.cache_dir.resolve(),
            asr_model=args.asr_model,
            fallback_asr_model=args.fallback_asr_model,
            device=args.device,
            compute_type=args.compute_type,
            ollama_model=args.ollama_model,
            ollama_url=args.ollama_url,
            ollama_keep_alive="5m",
            translation_batch_size=args.batch_size,
            translation_retries=args.translation_retries,
            overwrite=args.overwrite,
        )
        if args.probe:
            result = probe_environment(config.ollama_url, config.ollama_model)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 2
        report = run_pipeline(
            args.folder,
            config,
            dry_run=args.dry_run,
            transcribe_only=args.transcribe_only,
            translate_only=args.translate_only,
            release_ollama=not args.keep_ollama,
        )
        print(report.summary())
        for path, reason in report.failures:
            print(f"- {path}: {reason}", file=sys.stderr)
        return report.exit_code
    except (OSError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
