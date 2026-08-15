from __future__ import annotations

import json
import re
import time
import urllib.error
from collections.abc import Callable, Sequence

from .errors import TranslationError
from .models import Segment, TranslationItem
from .providers import OllamaProvider, ProviderConfig, TranslationProvider, create_provider
from .translation_context import CONTEXT_PROMPT_VERSION, ContextMemory

_JAPANESE_KANA = re.compile(r"[\u3040-\u30ff]")
_FORBIDDEN_OUTPUT = re.compile(
    r"```|思考过程|翻译说明|以下是|原文[：:]|作为(?:一名|一个)?AI", re.IGNORECASE
)

_SYSTEM = (
    "你是专业的 ASMR 对话字幕译者。先利用只读上下文理解话题、指代、省略、称谓和"
    "固定词义，再把目标行忠实翻译为自然、简洁、口语化的**简体中文**。只翻译 targets；"
    "context_before 和 context_after 只用于理解。不得补写原文没有的安慰、建议、动作、主语"
    "或结论，也不得把故意荒诞的内容纠正成常识。遇到明显 ASR 近音或漏字时，只有在人物、"
    "作品或连续话题证据很强时才按最小修正理解，无法确定就保守翻译。必须保持每个目标 ID，"
    "不得改写、合并、拆分、遗漏或新增。只返回指定 JSON，不输出日文假名、Markdown、解释、"
    "旁白、声音标签或思考过程。target_term_constraints 是目标行中已有原文证据的固定术语，"
    "必须在对应 ID 的中文中使用指定 target，不得换成近义误译或省略。"
)

_REVIEW_SYSTEM = (
    "你是 ASMR 字幕的终审译者。结合日文、上下文、术语和初译逐条核对，只修正真实的"
    "错译、漏译、指代、词义或不自然表达；正确的初译必须原样保留。不得合并或改写 ID，"
    "不得补写原文没有的信息。若日文转写本身疑似有误且无法高置信判断，将该 ID 放入"
    " uncertain_ids，但仍给出最保守的**中文**。target_term_constraints 是有原文证据的硬约束，"
    "对应中文必须使用指定 target。只返回指定 JSON。"
)

_TERM_REPAIR_SYSTEM = (
    "你只修复一条日语字幕的固定术语错误。忠实保留当前中文的其余含义和语气，"
    "让每个 required_terms.target 自然地出现在译文中；不得输出日文、解释或额外 ID。"
    "只返回指定 JSON。"
)


def check_model(base_url: str, model: str) -> None:
    OllamaProvider(ProviderConfig("ollama", base_url, model)).check()


def translation_schema(expected_ids: tuple[str, ...]) -> dict[str, object]:
    properties = {item_id: {"type": "string", "minLength": 1} for item_id in expected_ids}
    return {
        "type": "object",
        "properties": {
            "translations": {
                "type": "object",
                "properties": properties,
                "required": list(expected_ids),
                "additionalProperties": False,
            },
            "uncertain_ids": {
                "type": "array",
                "items": {"type": "string", "enum": list(expected_ids)},
                "uniqueItems": True,
            },
        },
        "required": ["translations", "uncertain_ids"],
        "additionalProperties": False,
    }


def _validate_translation_response(
    data: object,
    expected_ids: tuple[str, ...],
    *,
    strict_root: bool = False,
) -> tuple[tuple[TranslationItem, ...], tuple[str, ...]]:
    if not isinstance(data, dict):
        raise TranslationError("译文根节点必须是 JSON 对象")
    allowed = {"translations", "uncertain_ids"}
    if strict_root:
        if set(data) != allowed:
            raise TranslationError("译文根节点必须只包含 translations 和 uncertain_ids")
    elif set(data) not in ({"translations"}, allowed):
        raise TranslationError("译文根节点字段无效")

    raw_items = data.get("translations")
    items: list[TranslationItem] = []
    if isinstance(raw_items, dict):
        actual_ids = tuple(str(item_id) for item_id in raw_items)
        if set(actual_ids) != set(expected_ids) or len(actual_ids) != len(expected_ids):
            missing = sorted(set(expected_ids) - set(actual_ids))
            extra = sorted(set(actual_ids) - set(expected_ids))
            raise TranslationError(f"译文 ID 不匹配，缺少={missing}，额外={extra}")
        items = [
            TranslationItem(item_id, str(raw_items[item_id]).strip())
            for item_id in expected_ids
        ]
    elif isinstance(raw_items, list) and not strict_root:
        for raw in raw_items:
            if not isinstance(raw, dict) or set(raw) != {"id", "text"}:
                raise TranslationError("每条译文必须只包含 id 和 text")
            items.append(TranslationItem.from_dict(raw))
        actual_ids = tuple(item.id for item in items)
        if len(set(actual_ids)) != len(actual_ids):
            raise TranslationError("译文 ID 重复")
        if set(actual_ids) != set(expected_ids) or len(actual_ids) != len(expected_ids):
            missing = sorted(set(expected_ids) - set(actual_ids))
            extra = sorted(set(actual_ids) - set(expected_ids))
            raise TranslationError(f"译文 ID 不匹配，缺少={missing}，额外={extra}")
        by_id = {item.id: item for item in items}
        items = [by_id[item_id] for item_id in expected_ids]
    else:
        raise TranslationError("translations 必须是以精确 ID 为键的对象")

    for item in items:
        if not item.text:
            raise TranslationError(f"译文为空: {item.id}")
        if _FORBIDDEN_OUTPUT.search(item.text):
            raise TranslationError(f"译文包含说明或思考文本: {item.id}")
        if False:
            raise TranslationError(f"译文仍包含日文假名: {item.id}")

    raw_uncertain = data.get("uncertain_ids", [])
    if not isinstance(raw_uncertain, list):
        raise TranslationError("uncertain_ids 必须是数组")
    uncertain = tuple(str(item_id) for item_id in raw_uncertain)
    if len(set(uncertain)) != len(uncertain) or not set(uncertain).issubset(expected_ids):
        raise TranslationError("uncertain_ids 包含重复或未知 ID")
    return tuple(items), uncertain


def validate_translation(
    data: object, expected_ids: tuple[str, ...]
) -> tuple[TranslationItem, ...]:
    """Validate legacy/public translation data while returning items only."""
    items, _uncertain = _validate_translation_response(data, expected_ids)
    return items


def _segment_payload(segment: Segment, *, draft: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": segment.id,
        "start": round(segment.start, 3),
        "text": segment.text,
    }
    if draft is not None:
        payload["draft"] = draft
    return payload


def build_contextual_prompt(
    all_segments: Sequence[Segment],
    target_indices: tuple[int, ...],
    *,
    memory: ContextMemory,
    context_before: int,
    context_after: int,
    drafts: dict[str, str] | None = None,
    max_prompt_characters: int = 24_000,
    constraint_repair: bool = False,
) -> str:
    if not target_indices:
        raise ValueError("target_indices 不能为空")
    start = min(target_indices)
    end = max(target_indices) + 1
    before_start = max(0, start - context_before)
    after_end = min(len(all_segments), end + context_after)
    target_text = "\n".join(all_segments[index].text for index in target_indices)
    terms = list(memory.terms)
    terms.sort(
        key=lambda term: (
            term.source not in target_text,
            not term.pinned,
        )
    )
    if constraint_repair:
        terms = [term for term in terms if term.source in target_text]
    memory_payload = {
        "summary": "" if constraint_repair else memory.summary,
        "speaker_style": memory.speaker_style,
        "topics": [] if constraint_repair else list(memory.topics),
        "glossary": [term.to_dict() for term in terms],
    }
    before_indices: list[int] = []
    after_indices: list[int] = []
    target_term_constraints = {
        all_segments[index].id: [
            {"source": term.source, "target": term.target}
            for term in terms
            if term.pinned and term.source in all_segments[index].text
        ]
        for index in target_indices
    }

    def make_payload() -> dict[str, object]:
        return {
            "memory": memory_payload,
            "context_before": [
                _segment_payload(all_segments[index]) for index in sorted(before_indices)
            ],
            "targets": [
                _segment_payload(
                    all_segments[index],
                    draft=None if drafts is None else drafts.get(all_segments[index].id),
                )
                for index in target_indices
            ],
            "target_term_constraints": target_term_constraints,
            "context_after": [
                _segment_payload(all_segments[index]) for index in sorted(after_indices)
            ],
        }

    stage = "初译" if drafts is None else "终审"
    lexical_notes = (
        "术语表只在目标日文确实出现对应 source 时使用；带场景证据的固定译法优先于"
        "模型常见联想。注意否定、惯用句和文化词，不要擅自把荒诞设定改成常识。"
    )
    if constraint_repair:
        lexical_notes += (
            "本次为固定术语修复重试，已移除可能冲突的全局摘要和无关术语；"
            "必须优先遵守 target_term_constraints。"
        )

    def render(payload: dict[str, object], *, trimmed: bool = False) -> str:
        trim_note = "上下文已按字符预算保留最近邻内容。" if trimmed else ""
        return (
            f"执行{stage}。{lexical_notes}{trim_note}\n"
            "输出 translations 必须以 targets 中的精确 ID 为键；"
            "uncertain_ids 只能从这些 ID 中选择。\n"
            f"输入 JSON：{json.dumps(payload, ensure_ascii=False)}"
        )

    trimmed = False
    while len(render(make_payload(), trimmed=True)) > max_prompt_characters and memory_payload[
        "glossary"
    ]:
        memory_payload["glossary"].pop()  # type: ignore[union-attr]
        trimmed = True
    while len(render(make_payload(), trimmed=True)) > max_prompt_characters and memory_payload[
        "topics"
    ]:
        memory_payload["topics"].pop()  # type: ignore[union-attr]
        trimmed = True
    for key in ("summary", "speaker_style"):
        value = str(memory_payload[key])
        while len(render(make_payload(), trimmed=True)) > max_prompt_characters and value:
            excess = len(render(make_payload(), trimmed=True)) - max_prompt_characters
            value = value[: max(0, len(value) - max(1, excess))]
            memory_payload[key] = value
            trimmed = True

    candidates: list[tuple[str, int]] = []
    before_nearest = list(range(start - 1, before_start - 1, -1))
    after_nearest = list(range(end, after_end))
    for distance in range(max(len(before_nearest), len(after_nearest))):
        if distance < len(before_nearest):
            candidates.append(("before", before_nearest[distance]))
        if distance < len(after_nearest):
            candidates.append(("after", after_nearest[distance]))
    for side, index in candidates:
        destination = before_indices if side == "before" else after_indices
        destination.append(index)
        if len(render(make_payload(), trimmed=trimmed)) > max_prompt_characters:
            destination.pop()
            trimmed = True

    payload = make_payload()
    return render(payload, trimmed=trimmed)


def _confidence_flags(segment: Segment) -> list[str]:
    flags: list[str] = []
    probabilities = [word.probability for word in segment.words if word.probability is not None]
    low_word_confidence = bool(
        probabilities and sum(probabilities) / len(probabilities) < 0.55
    )
    low_segment_confidence = segment.avg_logprob is not None and segment.avg_logprob < -1.0
    if low_word_confidence or low_segment_confidence:
        flags.append("low_confidence")
    return flags


def _term_conflict(segment: Segment, text: str, memory: ContextMemory) -> bool:
    return any(
        term.source in segment.text and term.target not in text
        for term in memory.terms
        if term.source and term.target
    )


def _repair_pinned_terms(
    segment: Segment,
    text: str,
    *,
    provider: TranslationProvider,
    memory: ContextMemory,
) -> tuple[TranslationItem | None, dict[str, object] | None]:
    constraints = [
        {"source": term.source, "target": term.target}
        for term in memory.terms
        if term.pinned and term.source in segment.text and term.target not in text
    ]
    if not constraints:
        return None, None
    prompt = json.dumps(
        {
            "id": segment.id,
            "japanese": segment.text,
            "current_translation": text,
            "required_terms": constraints,
        },
        ensure_ascii=False,
    )
    try:
        response = provider.generate(
            system=_TERM_REPAIR_SYSTEM,
            prompt=f"修复以下 JSON：{prompt}",
            schema=translation_schema((segment.id,)),
        )
        items, _uncertain = _validate_translation_response(
            json.loads(response.text), (segment.id,), strict_root=True
        )
        repaired = items[0]
        if any(item["target"] not in repaired.text for item in constraints):
            return None, {**response.metrics, "validation": "term_conflict"}
        return repaired, {**response.metrics, "validation": "ok"}
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        urllib.error.URLError,
        TranslationError,
    ):
        return None, None


def _split_recoverable(error: Exception) -> bool:
    if isinstance(error, OSError | urllib.error.URLError):
        return False
    message = str(error)
    provider_failures = (
        "外部 API HTTP",
        "外部 API 请求失败",
        "无法连接 Ollama",
        "Ollama 模型未安装",
    )
    return not any(marker in message for marker in provider_failures)


def translate_contextual_batch(
    all_segments: Sequence[Segment],
    target_indices: tuple[int, ...],
    *,
    provider: TranslationProvider,
    memory: ContextMemory,
    context_before: int,
    context_after: int,
    retries: int,
    drafts: dict[str, str] | None = None,
    max_prompt_characters: int = 24_000,
) -> tuple[tuple[TranslationItem, ...], dict[str, object]]:
    targets = tuple(all_segments[index] for index in target_indices)
    expected_ids = tuple(segment.id for segment in targets)
    errors: list[str] = []
    last_error_recoverable = False
    for attempt in range(retries + 1):
        feedback = "" if not errors else f"\n上一次输出校验失败：{errors[-1]}"
        constraint_repair = any(error.startswith("固定术语未遵守") for error in errors)
        try:
            response = provider.generate(
                system=_SYSTEM if drafts is None else _REVIEW_SYSTEM,
                prompt=build_contextual_prompt(
                    all_segments,
                    target_indices,
                    memory=memory,
                    context_before=context_before,
                    context_after=context_after,
                    drafts=drafts,
                    max_prompt_characters=max_prompt_characters,
                    constraint_repair=constraint_repair,
                )
                + feedback,
                schema=translation_schema(expected_ids),
            )
            items, uncertain = _validate_translation_response(
                json.loads(response.text), expected_ids, strict_root=True
            )
            for segment, item in zip(targets, items, strict=True):
                missing = [
                    f"{term.source}=>{term.target}"
                    for term in memory.terms
                    if term.pinned
                    and term.source in segment.text
                    and term.target not in item.text
                ]
                if missing and attempt < retries:
                    raise TranslationError(
                        f"固定术语未遵守: {segment.id}: {', '.join(missing)}"
                    )
            repaired_ids: set[str] = set()
            repair_metrics: list[dict[str, object]] = []
            if attempt == retries:
                repaired_items = list(items)
                for index, (segment, item) in enumerate(zip(targets, items, strict=True)):
                    if not _term_conflict(segment, item.text, memory):
                        continue
                    repaired, metrics = _repair_pinned_terms(
                        segment,
                        item.text,
                        provider=provider,
                        memory=memory,
                    )
                    if metrics is not None:
                        repair_metrics.append({**metrics, "id": segment.id})
                    if repaired is not None:
                        repaired_items[index] = repaired
                        repaired_ids.add(segment.id)
                items = tuple(repaired_items)
            result: list[TranslationItem] = []
            for segment, item in zip(targets, items, strict=True):
                flags = _confidence_flags(segment)
                if segment.id in uncertain:
                    flags.append("asr_suspect")
                if drafts is not None and drafts.get(segment.id) != item.text:
                    flags.append("review_changed")
                if segment.id in repaired_ids:
                    flags.append("term_repaired")
                if _term_conflict(segment, item.text, memory):
                    flags.append("term_conflict")
                result.append(TranslationItem(item.id, item.text, tuple(dict.fromkeys(flags))))
            return tuple(result), {
                **response.metrics,
                "stage": "draft" if drafts is None else "review",
                "ids": list(expected_ids),
                "attempts": attempt + 1,
                "validation": "ok",
                "prompt_version": CONTEXT_PROMPT_VERSION,
                "term_repairs": repair_metrics,
            }
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            urllib.error.URLError,
            TranslationError,
        ) as exc:
            errors.append(str(exc))
            last_error_recoverable = _split_recoverable(exc)
            if attempt < retries:
                time.sleep(min(2**attempt, 4))
    if len(target_indices) > 1 and last_error_recoverable:
        midpoint = len(target_indices) // 2
        split_indices = (target_indices[:midpoint], target_indices[midpoint:])
        recovered: list[TranslationItem] = []
        split_metrics: list[dict[str, object]] = []
        for indices in split_indices:
            items, metrics = translate_contextual_batch(
                all_segments,
                indices,
                provider=provider,
                memory=memory,
                context_before=context_before,
                context_after=context_after,
                retries=retries,
                drafts=drafts,
                max_prompt_characters=max_prompt_characters,
            )
            recovered.extend(items)
            split_metrics.append(metrics)
        return tuple(recovered), {
            "stage": "draft" if drafts is None else "review",
            "ids": list(expected_ids),
            "attempts": retries + 1,
            "validation": "recovered_by_split",
            "prompt_version": CONTEXT_PROMPT_VERSION,
            "errors": errors,
            "split_metrics": split_metrics,
        }
    raise TranslationError(
        f"翻译在 {retries + 1} 次尝试后失败: {' | '.join(errors)}"
    )


def translate_batch(
    segments: tuple[Segment, ...],
    *,
    base_url: str,
    model: str,
    retries: int,
    keep_alive: str,
) -> tuple[tuple[TranslationItem, ...], dict[str, object]]:
    provider = create_provider(
        ProviderConfig("ollama", base_url, model, keep_alive=keep_alive)
    )
    return translate_contextual_batch(
        segments,
        tuple(range(len(segments))),
        provider=provider,
        memory=ContextMemory(),
        context_before=0,
        context_after=0,
        retries=retries,
    )


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
    provider = create_provider(
        ProviderConfig("ollama", base_url, model, keep_alive=keep_alive)
    )
    provider.check()
    translated: list[TranslationItem] = []
    metrics: list[dict[str, object]] = []
    total_batches = (len(segments) + batch_size - 1) // batch_size
    for offset in range(0, len(segments), batch_size):
        batch_number = offset // batch_size + 1
        if progress is not None:
            progress(batch_number, total_batches)
        indices = tuple(range(offset, min(len(segments), offset + batch_size)))
        items, batch_metrics = translate_contextual_batch(
            segments,
            indices,
            provider=provider,
            memory=ContextMemory(),
            context_before=0,
            context_after=0,
            retries=retries,
        )
        translated.extend(items)
        metrics.append(batch_metrics)
    return tuple(translated), tuple(metrics)


def unload_model(base_url: str, model: str) -> None:
    OllamaProvider(ProviderConfig("ollama", base_url, model)).unload()
