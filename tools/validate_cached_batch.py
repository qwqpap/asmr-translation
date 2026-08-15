from __future__ import annotations

import argparse
import json
from pathlib import Path

from asmr_lrc.cache import load_validated
from asmr_lrc.models import FilteredTranscript
from asmr_lrc.translator import translate_batch, unload_model


def main() -> int:
    parser = argparse.ArgumentParser(description="对已有过滤缓存做单批 Ollama 冒烟")
    parser.add_argument("filtered", type=Path)
    parser.add_argument("--model", default="translategemma:4b")
    parser.add_argument(
        "--protocol", choices=("chat-json", "translategemma"), help="提示协议；默认按模型名推断"
    )
    parser.add_argument("--url", default="http://127.0.0.1:11434")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--count", type=int, default=4)
    args = parser.parse_args()
    filtered = load_validated(args.filtered, FilteredTranscript.from_dict)
    segments = filtered.accepted[args.offset : args.offset + args.count]
    try:
        items, metrics = translate_batch(
            segments,
            base_url=args.url,
            model=args.model,
            retries=1,
            keep_alive="5m",
            protocol=args.protocol,
        )
        print(
            json.dumps(
                {
                    "count": len(items),
                    "translations": [{"id": item.id, "text": item.text} for item in items],
                    "metrics": metrics,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        unload_model(args.url, args.model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
