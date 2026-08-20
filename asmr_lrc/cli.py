from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .config import (
    DEFAULT_ANALYSIS_MODEL,
    DEFAULT_TRANSLATION_MODEL,
    AppConfig,
    protocol_for_model,
)
from .environment import probe_environment
from .pipeline import run_pipeline
from .providers import ProviderConfig, create_provider
from .translation_context import load_pinned_glossary


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
    parser.add_argument("--ffmpeg-path", default="ffmpeg", help="FFmpeg 可执行文件路径")
    parser.add_argument("--ollama-model", default=DEFAULT_TRANSLATION_MODEL)
    parser.add_argument(
        "--ollama-protocol",
        choices=("chat-json", "translategemma"),
        help="主翻译模型提示协议；默认按模型名自动判断",
    )
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument(
        "--analysis-model", help="语境分析模型；TranslateGemma 默认使用当前 Qwen 模型"
    )
    parser.add_argument(
        "--analysis-protocol",
        choices=("chat-json", "translategemma"),
        help="语境分析提示协议；默认按模型名自动判断",
    )
    parser.add_argument("--analysis-url", help="语境分析 Ollama 地址；默认使用主翻译地址")
    parser.add_argument("--fallback-model", help="主翻译失败时使用的兜底模型")
    parser.add_argument(
        "--fallback-protocol",
        choices=("chat-json", "translategemma"),
        help="失败兜底提示协议；默认按模型名自动判断",
    )
    parser.add_argument("--fallback-url", help="兜底 Ollama 地址；默认使用语境分析地址")
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--translation-retries", type=int, default=2)
    parser.add_argument("--quality-mode", choices=("quality", "balanced"), default="quality")
    parser.add_argument("--no-review", action="store_true", help="关闭二次审校")
    parser.add_argument("--context-before", type=int, default=8)
    parser.add_argument("--context-after", type=int, default=8)
    parser.add_argument(
        "--glossary",
        type=Path,
        help="可跨同一资料库复用的固定术语 JSON 文件",
    )
    parser.add_argument("--draft-provider", choices=("ollama", "openai"), default="ollama")
    parser.add_argument(
        "--review-provider", choices=("same", "ollama", "openai"), default="same"
    )
    parser.add_argument("--review-model", help="审校模型；默认与对应提供方模型相同")
    parser.add_argument("--review-base-url", help="审校提供方地址；默认与对应提供方相同")
    parser.add_argument("--openai-base-url", default="https://api.openai.com/v1")
    parser.add_argument("--openai-model", default="")
    parser.add_argument(
        "--openai-api-key-env",
        default="ASMR_TRANSLATION_API_KEY",
        help="保存外部 API Key 的环境变量名",
    )
    parser.add_argument(
        "--allow-external-text",
        action="store_true",
        help="非交互运行时明确允许向配置的外部 API 发送转写文本（永不上传音频）",
    )
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
        openai_key = os.environ.get(args.openai_api_key_env)
        if args.draft_provider == "ollama":
            draft_provider = ProviderConfig(
                "ollama",
                args.ollama_url,
                args.ollama_model,
                keep_alive="5m",
                protocol=args.ollama_protocol or protocol_for_model(args.ollama_model),
            )
        else:
            draft_provider = ProviderConfig(
                "openai",
                args.openai_base_url,
                args.openai_model,
                api_key=openai_key,
            )
        analysis_provider = None
        if args.analysis_model:
            analysis_provider = ProviderConfig(
                "ollama",
                args.analysis_url or args.ollama_url,
                args.analysis_model,
                keep_alive="5m",
                protocol=args.analysis_protocol or protocol_for_model(args.analysis_model),
            )
        elif draft_provider.protocol == "translategemma":
            analysis_provider = ProviderConfig(
                "ollama",
                args.ollama_url,
                DEFAULT_ANALYSIS_MODEL,
                keep_alive="5m",
                protocol="chat-json",
            )
        fallback_provider = None
        if args.fallback_model:
            fallback_provider = ProviderConfig(
                "ollama",
                args.fallback_url or (args.analysis_url or args.ollama_url),
                args.fallback_model,
                keep_alive="5m",
                protocol=args.fallback_protocol or protocol_for_model(args.fallback_model),
            )
        elif analysis_provider is not None:
            fallback_provider = analysis_provider
        if args.no_review:
            review_provider = None
        elif args.review_provider == "same":
            review_provider = draft_provider
        elif args.review_provider == "ollama":
            review_provider = ProviderConfig(
                "ollama",
                args.review_base_url or args.ollama_url,
                args.review_model or args.ollama_model,
                keep_alive="5m",
                protocol=protocol_for_model(args.review_model or args.ollama_model),
            )
        else:
            review_provider = ProviderConfig(
                "openai",
                args.review_base_url or args.openai_base_url,
                args.review_model or args.openai_model,
                api_key=openai_key,
            )
        config = AppConfig(
            cache_root=args.cache_dir.resolve(),
            asr_model=args.asr_model,
            fallback_asr_model=args.fallback_asr_model,
            device=args.device,
            compute_type=args.compute_type,
            ffmpeg_path=args.ffmpeg_path,
            ollama_model=args.ollama_model,
            ollama_url=args.ollama_url,
            ollama_keep_alive="5m",
            translation_batch_size=args.batch_size,
            translation_retries=args.translation_retries,
            quality_mode=args.quality_mode,
            context_before=args.context_before,
            context_after=args.context_after,
            review_enabled=not args.no_review and draft_provider.protocol != "translategemma",
            draft_provider=draft_provider,
            review_provider=review_provider,
            analysis_provider=analysis_provider,
            fallback_provider=fallback_provider,
            pinned_glossary=(
                () if args.glossary is None else load_pinned_glossary(args.glossary.resolve())
            ),
            overwrite=args.overwrite,
        )
        if args.probe:
            providers = [config.draft_provider]
            for provider in (
                config.review_provider,
                config.analysis_provider,
                config.fallback_provider,
            ):
                if provider is not None and not any(
                    provider == existing for existing in providers if existing is not None
                ):
                    providers.append(provider)
            ollama_provider = next(
                (
                    provider
                    for provider in providers
                    if provider is not None and provider.kind == "ollama"
                ),
                None,
            )
            result = probe_environment(
                None if ollama_provider is None else ollama_provider.base_url,
                None if ollama_provider is None else ollama_provider.model,
                ffmpeg_path=config.ffmpeg_path,
            )
            provider_checks = []
            for provider_config in providers:
                if provider_config is None:
                    continue
                try:
                    create_provider(provider_config).check()
                    provider_checks.append(
                        {
                            "kind": provider_config.kind,
                            "model": provider_config.model,
                            "ok": True,
                        }
                    )
                except Exception as exc:
                    item = {
                        "kind": provider_config.kind,
                        "model": provider_config.model,
                        "ok": False,
                        "detail": str(exc),
                    }
                    if provider_config.kind == "ollama" and "模型未安装" in str(exc):
                        item["install_command"] = f"ollama pull {provider_config.model}"
                    provider_checks.append(item)
            result["provider_checks"] = provider_checks
            result["ok"] = bool(result["ok"]) and all(
                bool(item["ok"]) for item in provider_checks
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 2

        def confirm_external_text(estimated_characters: int) -> bool:
            print(
                f"外部 API 预计接收约 {estimated_characters} 个转写文本字符；音频不会上传，"
                "API Key 不会写入配置、缓存、日志或命令行。",
                file=sys.stderr,
            )
            if args.allow_external_text:
                return True
            if not sys.stdin.isatty():
                print(
                    "非交互运行需要显式添加 --allow-external-text。",
                    file=sys.stderr,
                )
                return False
            answer = input("是否继续？[y/N] ").strip().casefold()
            return answer in {"y", "yes"}

        report = run_pipeline(
            args.folder,
            config,
            dry_run=args.dry_run,
            transcribe_only=args.transcribe_only,
            translate_only=args.translate_only,
            release_ollama=not args.keep_ollama,
            external_consent_callback=confirm_external_text,
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
