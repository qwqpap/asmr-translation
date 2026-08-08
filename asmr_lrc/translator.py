from __future__ import annotations

import json
import re
import time
import urllib.error
from collections.abc import Callable

from .environment import ollama_models, ollama_request
from .errors import TranslationError
from .models import Segment, TranslationItem

_JAPANESE_KANA = re.compile(r"[\u3040-\u30ff]")
_FORBIDDEN_OUTPUT = re.compile(r"```|思考过程|翻译说明|以下是|原文[：:]", re.IGNORECASE)


def check_model(base_url: str, model: str) -> None:
    try:
        models = ollama_models(base_url)
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        raise TranslationError(f"无法连接 Ollama 服务 {base_url}: {exc}") from exc
    if model not in models:
        raise TranslationError(f"Ollama 模型未安装: {model}")


def _prompt(segments: tuple[Segment, ...]) -> str:
    payload = [{"id": item.id, "text": item.text} for item in segments]
    return (
        "你是日语 ASMR 字幕翻译器。将输入逐条忠实翻译成自然、简洁的简体中文。"
        "只翻译原句明确表达的语义，严禁补充原文没有的安慰、建议、动作、主语或结论；"
        "例如原文只有‘辛苦了’时，不得添加‘好好休息’。"
        "保留每个 id，不得遗漏、合并或新增项目。只返回符合指定 JSON schema 的对象。"
        "不要输出日文原文、Markdown、解释、旁白、思考过程或声音标签。"
        "若内容确实无法翻译，也必须保留 id，并给出最保守的中文表达。\n"
        f"输入 JSON：{json.dumps(payload, ensure_ascii=False)}"
    )


def _schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "translations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["id", "text"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["translations"],
        "additionalProperties": False,
    }


def validate_translation(
    data: object, expected_ids: tuple[str, ...]
) -> tuple[TranslationItem, ...]:
    if not isinstance(data, dict) or set(data) != {"translations"}:
        raise TranslationError("译文根节点必须只包含 translations")
    raw_items = data["translations"]
    if not isinstance(raw_items, list):
        raise TranslationError("translations 必须是数组")
    items: list[TranslationItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict) or set(raw) != {"id", "text"}:
            raise TranslationError("每条译文必须只包含 id 和 text")
        item = TranslationItem.from_dict(raw)
        if not item.text:
            raise TranslationError(f"译文为空: {item.id}")
        if _FORBIDDEN_OUTPUT.search(item.text):
            raise TranslationError(f"译文包含说明或思考文本: {item.id}")
        if _JAPANESE_KANA.search(item.text):
            raise TranslationError(f"译文仍包含日文假名: {item.id}")
        items.append(item)
    actual_ids = tuple(item.id for item in items)
    if len(set(actual_ids)) != len(actual_ids):
        raise TranslationError("译文 ID 重复")
    if set(actual_ids) != set(expected_ids) or len(actual_ids) != len(expected_ids):
        missing = sorted(set(expected_ids) - set(actual_ids))
        extra = sorted(set(actual_ids) - set(expected_ids))
        raise TranslationError(f"译文 ID 不匹配，缺少={missing}，额外={extra}")
    by_id = {item.id: item for item in items}
    return tuple(by_id[item_id] for item_id in expected_ids)


def translate_batch(
    segments: tuple[Segment, ...],
    *,
    base_url: str,
    model: str,
    retries: int,
    keep_alive: str,
) -> tuple[tuple[TranslationItem, ...], dict[str, object]]:
    expected_ids = tuple(item.id for item in segments)
    errors: list[str] = []
    for attempt in range(retries + 1):
        request_body = {
            "model": model,
            "prompt": _prompt(segments),
            "stream": False,
            "think": False,
            "format": _schema(),
            "keep_alive": keep_alive,
            "options": {"temperature": 0.1, "seed": 0},
        }
        try:
            response = ollama_request(
                base_url,
                "/api/generate",
                payload=request_body,
                timeout=600,
            )
            raw = response.get("response")
            if not isinstance(raw, str):
                raise TranslationError("Ollama 响应缺少 response 字符串")
            items = validate_translation(json.loads(raw), expected_ids)
            metrics: dict[str, object] = {
                "ids": list(expected_ids),
                "attempts": attempt + 1,
                "validation": "ok",
                "total_duration_ns": response.get("total_duration"),
                "eval_count": response.get("eval_count"),
            }
            return items, metrics
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            urllib.error.URLError,
            TranslationError,
        ) as exc:
            errors.append(str(exc))
            if attempt < retries:
                time.sleep(min(2**attempt, 4))
    raise TranslationError(f"翻译在 {retries + 1} 次尝试后失败: {' | '.join(errors)}")


def translate_segments(
    segments: tuple[Segment, ...],
    *,
    base_url: str,
    model: str,
    batch_size: int,
    retries: int,
    keep_alive: str,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[tuple[TranslationItem, ...], tuple[dict[str, object], ...]]:
    check_model(base_url, model)
    translated: list[TranslationItem] = []
    metrics: list[dict[str, object]] = []
    total_batches = (len(segments) + batch_size - 1) // batch_size
    for offset in range(0, len(segments), batch_size):
        batch_number = offset // batch_size + 1
        if progress is not None:
            progress(batch_number, total_batches)
        batch = segments[offset : offset + batch_size]
        items, batch_metrics = translate_batch(
            batch,
            base_url=base_url,
            model=model,
            retries=retries,
            keep_alive=keep_alive,
        )
        translated.extend(items)
        metrics.append(batch_metrics)
    return tuple(translated), tuple(metrics)


def unload_model(base_url: str, model: str) -> None:
    try:
        ollama_request(
            base_url,
            "/api/generate",
            payload={"model": model, "keep_alive": 0},
            timeout=30,
        )
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        raise TranslationError(f"无法卸载 Ollama 模型 {model}: {exc}") from exc
