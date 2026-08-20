from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from asmr_lrc.models import Segment
from asmr_lrc.providers import ProviderConfig, create_provider
from asmr_lrc.translation_context import analyze_context, baseline_context_memory
from asmr_lrc.translator import translate_contextual_batch


def _provider(args: argparse.Namespace) -> ProviderConfig:
    if args.provider == "ollama":
        return ProviderConfig("ollama", args.base_url, args.model, keep_alive="5m")
    key = os.environ.get(args.api_key_env)
    return ProviderConfig("openai", args.base_url, args.model, api_key=key)


def _passes(text: str, expectation: dict[str, Any]) -> bool:
    required = [str(value) for value in expectation.get("required_any", [])]
    forbidden = [str(value) for value in expectation.get("forbidden", [])]
    return (not required or any(value in text for value in required)) and not any(
        value in text for value in forbidden
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 30 条上下文翻译质量基准")
    parser.add_argument(
        "fixture",
        nargs="?",
        type=Path,
        default=Path("tests/fixtures/context_quality_cases.json"),
    )
    parser.add_argument("--provider", choices=("ollama", "openai"), default="ollama")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="translategemma:4b")
    parser.add_argument("--analysis-model", default="qwen3.5-9b-abliterated:latest")
    parser.add_argument("--api-key-env", default="ASMR_TRANSLATION_API_KEY")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--no-review", action="store_true")
    parser.add_argument("--no-context-analysis", action="store_true")
    parser.add_argument("--scene", action="append", help="只运行指定场景名，可重复")
    parser.add_argument("--show-memory", action="store_true")
    args = parser.parse_args()

    data = json.loads(args.fixture.read_text(encoding="utf-8"))
    provider = create_provider(_provider(args))
    analysis_provider = (
        provider
        if args.no_context_analysis
        else create_provider(
            ProviderConfig("ollama", args.base_url, args.analysis_model, keep_alive="5m")
        )
    )
    provider.check()
    if analysis_provider is not provider:
        analysis_provider.check()
    results: list[dict[str, object]] = []
    analysis_unloaded = False
    try:
        scenes = [
            scene
            for scene in data["scenes"]
            if not args.scene or scene["name"] in set(args.scene)
        ]
        if not scenes:
            raise ValueError("没有匹配 --scene 的质量场景")
        prepared: list[tuple[dict[str, Any], tuple[Segment, ...], tuple[int, ...], Any]] = []
        for scene_number, scene in enumerate(scenes, 1):
            print(
                f"[context {scene_number}/{len(scenes)}] {scene['name']}",
                file=sys.stderr,
                flush=True,
            )
            segments = tuple(
                Segment(str(item["id"]), index, index + 0.8, str(item["text"]))
                for index, item in enumerate(scene["segments"])
            )
            expected_ids = {str(item["id"]) for item in scene["expectations"]}
            indices = tuple(
                index for index, segment in enumerate(segments) if segment.id in expected_ids
            )
            memory = baseline_context_memory(segments)
            if not args.no_context_analysis:
                memory, _context_metrics = analyze_context(
                    segments,
                    provider=analysis_provider,
                    retries=args.retries,
                )
                if args.show_memory:
                    print(
                        json.dumps(memory.to_dict(), ensure_ascii=False, indent=2),
                        file=sys.stderr,
                    )
            prepared.append((scene, segments, indices, memory))

        if analysis_provider is not provider:
            analysis_provider.unload()
            analysis_unloaded = True

        for scene_number, (scene, segments, indices, memory) in enumerate(prepared, 1):
            print(
                f"[translate {scene_number}/{len(prepared)}] {scene['name']}",
                file=sys.stderr,
                flush=True,
            )
            draft, _draft_metrics = translate_contextual_batch(
                segments,
                indices,
                provider=provider,
                memory=memory,
                context_before=8,
                context_after=8,
                retries=args.retries,
            )
            final = draft
            if not args.no_review:
                final, _review_metrics = translate_contextual_batch(
                    segments,
                    indices,
                    provider=provider,
                    memory=memory,
                    context_before=8,
                    context_after=8,
                    retries=args.retries,
                    drafts={item.id: item.text for item in draft},
                )
            draft_by_id = {item.id: item.text for item in draft}
            by_id = {item.id: item.text for item in final}
            for expectation in scene["expectations"]:
                item_id = str(expectation["id"])
                text = by_id[item_id]
                results.append(
                    {
                        "scene": scene["name"],
                        "id": item_id,
                        "category": expectation["category"],
                        "critical": bool(expectation.get("critical", False)),
                        "passed": _passes(text, expectation),
                        "draft_text": draft_by_id[item_id],
                        "review_changed": draft_by_id[item_id] != text,
                        "text": text,
                    }
                )
    finally:
        provider.unload()
        if analysis_provider is not provider and not analysis_unloaded:
            analysis_provider.unload()

    fixable = [item for item in results if item["category"] == "translation_fixable"]
    critical = [item for item in results if item["critical"]]
    output = {
        "total": len(results),
        "passed": sum(bool(item["passed"]) for item in results),
        "translation_fixable": {
            "total": len(fixable),
            "passed": sum(bool(item["passed"]) for item in fixable),
            "ratio": round(sum(bool(item["passed"]) for item in fixable) / len(fixable), 3),
        },
        "critical_failures": [item for item in critical if not item["passed"]],
        "failures": [item for item in results if not item["passed"]],
        "results": results,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    accepted = (
        output["translation_fixable"]["ratio"] >= 0.8
        and not output["critical_failures"]
    )
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
