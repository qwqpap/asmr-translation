from __future__ import annotations

import argparse
import json

from asmr_lrc.models import Segment
from asmr_lrc.translator import translate_batch, unload_model

SAMPLES = (
    Segment("sample-1", 0, 1, "今日は一日お疲れさまでした。"),
    Segment("sample-2", 1, 2, "まずは右耳からゆっくり始めますね。"),
    Segment("sample-3", 2, 3, "痛かったらすぐに言ってください。"),
    Segment("sample-4", 3, 4, "少し近くで囁いてもいいですか。"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="验证 Ollama 日中翻译 JSON 协议稳定性")
    parser.add_argument("--model", default="translategemma:4b")
    parser.add_argument(
        "--protocol",
        choices=("chat-json", "translategemma"),
        help="提示协议；默认按模型名推断",
    )
    parser.add_argument("--url", default="http://127.0.0.1:11434")
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds 必须大于 0")

    results: list[dict[str, object]] = []
    try:
        for round_index in range(args.rounds):
            items, metrics = translate_batch(
                SAMPLES,
                base_url=args.url,
                model=args.model,
                retries=2,
                keep_alive="5m",
                protocol=args.protocol,
            )
            results.append(
                {
                    "round": round_index + 1,
                    "translations": [
                        {"id": item.id, "text": item.text} for item in items
                    ],
                    "metrics": metrics,
                }
            )
    finally:
        unload_model(args.url, args.model)
    print(json.dumps({"model": args.model, "rounds": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
