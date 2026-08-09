from __future__ import annotations

import json
import re
import time
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import TranslationError
from .models import Segment
from .providers import TranslationProvider

CONTEXT_SCHEMA_VERSION = 1
CONTEXT_PROMPT_VERSION = "context-v4"
GLOSSARY_SCHEMA_VERSION = 1
_JAPANESE_KANA = re.compile(r"[\u3040-\u30ff]")
_CJK = re.compile(r"[\u3400-\u9fff]")


def _valid_chinese_memory_text(value: str) -> bool:
    if not value:
        return True
    if _JAPANESE_KANA.search(value) or _CJK.search(value) is None:
        return False
    ascii_letters = sum(character.isascii() and character.isalpha() for character in value)
    cjk_characters = sum("\u3400" <= character <= "\u9fff" for character in value)
    return ascii_letters <= max(12, cjk_characters * 2)


@dataclass(frozen=True, slots=True)
class GlossaryTerm:
    source: str
    target: str
    evidence_ids: tuple[str, ...] = ()
    pinned: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GlossaryTerm:
        return cls(
            source=str(data["source"]).strip(),
            target=str(data["target"]).strip(),
            evidence_ids=tuple(str(value) for value in data.get("evidence_ids", [])),
            pinned=bool(data.get("pinned", False)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "target": self.target,
            "evidence_ids": list(self.evidence_ids),
            "pinned": self.pinned,
        }


@dataclass(frozen=True, slots=True)
class _BuiltinGlossaryRule:
    source: str
    target: str
    anchors: tuple[str, ...] = ()


_BUILTIN_GLOSSARY = (
    _BuiltinGlossaryRule("お姉さんこう見えても", "别看姐姐这样"),
    _BuiltinGlossaryRule("徳川将軍", "德川幕府将军"),
    _BuiltinGlossaryRule("滑り台", "滑梯"),
    _BuiltinGlossaryRule("松ぼっくり", "松果"),
    _BuiltinGlossaryRule("松やに", "松脂"),
    _BuiltinGlossaryRule("寒天", "琼脂"),
    _BuiltinGlossaryRule("おやつ", "点心"),
    _BuiltinGlossaryRule("リモコン", "遥控器"),
    _BuiltinGlossaryRule("わかるわけない", "怎么可能知道"),
    _BuiltinGlossaryRule("どっちか", "其中一个"),
    _BuiltinGlossaryRule("パワースポット", "能量圣地"),
    _BuiltinGlossaryRule("ひな祭り", "女儿节"),
    _BuiltinGlossaryRule("かまぼこ", "鱼糕"),
    _BuiltinGlossaryRule("帰宅部", "回家部"),
    _BuiltinGlossaryRule("助っ人", "帮工"),
    _BuiltinGlossaryRule("カプチーノ", "卡布奇诺"),
    _BuiltinGlossaryRule("背脂", "猪背脂"),
    _BuiltinGlossaryRule("坊っちゃん", "《少爷》"),
    _BuiltinGlossaryRule("ぽっちゃん", "《少爷》", ("夏目漱石",)),
    _BuiltinGlossaryRule("3サイズ", "三个尺码", ("コート", "クリーニング")),
)


def builtin_glossary_for_segments(segments: tuple[Segment, ...]) -> tuple[GlossaryTerm, ...]:
    corpus = "\n".join(segment.text for segment in segments)
    terms: list[GlossaryTerm] = []
    for rule in _BUILTIN_GLOSSARY:
        if rule.anchors and not all(anchor in corpus for anchor in rule.anchors):
            continue
        evidence = tuple(segment.id for segment in segments if rule.source in segment.text)
        if evidence:
            terms.append(GlossaryTerm(rule.source, rule.target, evidence, pinned=True))
    return tuple(terms)


@dataclass(frozen=True, slots=True)
class ContextMemory:
    summary: str = ""
    speaker_style: str = ""
    topics: tuple[str, ...] = ()
    terms: tuple[GlossaryTerm, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextMemory:
        version = int(data.get("schema_version", 0))
        if version != CONTEXT_SCHEMA_VERSION:
            raise ValueError(f"不支持的 context schema_version: {version}")
        return cls(
            summary=str(data.get("summary", "")).strip(),
            speaker_style=str(data.get("speaker_style", "")).strip(),
            topics=tuple(
                str(value).strip()
                for value in data.get("topics", [])
                if str(value).strip()
            ),
            terms=tuple(GlossaryTerm.from_dict(value) for value in data.get("terms", [])),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CONTEXT_SCHEMA_VERSION,
            "summary": self.summary,
            "speaker_style": self.speaker_style,
            "topics": list(self.topics),
            "terms": [term.to_dict() for term in self.terms],
        }


def load_pinned_glossary(path: Path) -> tuple[GlossaryTerm, ...]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取固定术语表 {path}: {exc}") from exc
    if not isinstance(data, dict) or int(data.get("schema_version", 0)) != GLOSSARY_SCHEMA_VERSION:
        raise ValueError("固定术语表必须是 schema_version=1 的 JSON 对象")
    raw_terms = data.get("terms")
    if not isinstance(raw_terms, list):
        raise ValueError("固定术语表 terms 必须是数组")
    terms: list[GlossaryTerm] = []
    by_source: dict[str, str] = {}
    for raw in raw_terms:
        if not isinstance(raw, dict):
            raise ValueError("固定术语表的每一项必须是对象")
        term = GlossaryTerm.from_dict({**raw, "pinned": True})
        if not term.source or not term.target:
            raise ValueError("固定术语的 source 和 target 不能为空")
        existing = by_source.get(term.source)
        if existing is not None and existing != term.target:
            raise ValueError(f"固定术语存在冲突: {term.source}")
        if existing is None:
            by_source[term.source] = term.target
            terms.append(term)
    return tuple(terms)


def context_schema(segment_ids: tuple[str, ...]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "speaker_style": {"type": "string"},
            "topics": {"type": "array", "items": {"type": "string"}},
            "terms": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "target": {"type": "string"},
                        "evidence_ids": {
                            "type": "array",
                            "items": {"type": "string", "enum": list(segment_ids)},
                            "uniqueItems": True,
                        },
                    },
                    "required": ["source", "target", "evidence_ids"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "speaker_style", "topics", "terms"],
        "additionalProperties": False,
    }


_SYSTEM = (
    "你负责为日语 ASMR 对话建立翻译语境记忆。只总结输入明确提供的信息，"
    "不得补写剧情或把故意荒诞的内容纠正成常识。只返回指定 JSON。"
)


def _valid_glossary_target(value: str) -> bool:
    return _valid_chinese_memory_text(value) and not any(
        character in value for character in "()（）"
    )


def _prompt(segments: tuple[Segment, ...]) -> str:
    payload = [{"id": segment.id, "text": segment.text} for segment in segments]
    return (
        "通读按时间排序的日文转写，提取后续翻译真正需要的简短语境：人物关系或称谓、"
        "说话风格、话题，以及容易误译的专名、物品或固定词。每个术语必须引用至少一个"
        "确实出现该日文词的 evidence_id；不确定的项目不要写入术语表。target 只填写最终"
        "简体中文译法，不得保留日文、罗马字、注音或括号解释。\n"
        f"输入 JSON：{json.dumps(payload, ensure_ascii=False)}"
    )


def validate_context(
    data: object,
    segments: tuple[Segment, ...],
    *,
    drop_invalid_terms: bool = False,
) -> ContextMemory:
    if not isinstance(data, dict) or set(data) != {
        "summary",
        "speaker_style",
        "topics",
        "terms",
    }:
        raise TranslationError("语境响应字段不完整或包含额外字段")
    ids = {segment.id: segment for segment in segments}
    if not isinstance(data["topics"], list) or not isinstance(data["terms"], list):
        raise TranslationError("语境 topics/terms 必须是数组")
    summary = str(data["summary"]).strip()
    speaker_style = str(data["speaker_style"]).strip()
    topics = tuple(str(value).strip() for value in data["topics"] if str(value).strip())
    invalid_narrative = [
        value
        for value in (summary, speaker_style, *topics)
        if not _valid_chinese_memory_text(value)
    ]
    if invalid_narrative and not drop_invalid_terms:
        raise TranslationError("语境摘要、风格和话题必须使用简体中文")
    if drop_invalid_terms:
        summary = summary if _valid_chinese_memory_text(summary) else ""
        speaker_style = (
            speaker_style if _valid_chinese_memory_text(speaker_style) else ""
        )
        topics = tuple(value for value in topics if _valid_chinese_memory_text(value))
    terms: list[GlossaryTerm] = []
    for raw in data["terms"]:
        if not isinstance(raw, dict) or set(raw) != {"source", "target", "evidence_ids"}:
            raise TranslationError("语境术语字段无效")
        term = GlossaryTerm.from_dict(raw)
        if (
            not term.source
            or not term.target
            or not term.evidence_ids
            or _JAPANESE_KANA.search(term.target)
            or not _valid_glossary_target(term.target)
        ):
            if drop_invalid_terms:
                continue
            raise TranslationError("语境术语缺少原文、中文译文或证据")
        if any(evidence_id not in ids for evidence_id in term.evidence_ids):
            if drop_invalid_terms:
                continue
            raise TranslationError("语境术语引用了不存在的 evidence_id")
        compact_source = re.sub(r"\s+", "", term.source)
        if not any(
            compact_source in re.sub(r"\s+", "", ids[evidence_id].text)
            for evidence_id in term.evidence_ids
        ):
            if drop_invalid_terms:
                continue
            raise TranslationError(f"语境术语没有原文证据: {term.source}")
        terms.append(term)
    return ContextMemory(
        summary=summary,
        speaker_style=speaker_style,
        topics=topics,
        terms=tuple(terms),
    )


def _merge_memories(
    memories: list[ContextMemory], pinned_terms: tuple[GlossaryTerm, ...]
) -> ContextMemory:
    terms: dict[str, GlossaryTerm] = {term.source: term for term in pinned_terms}
    for memory in memories:
        for term in memory.terms:
            existing = terms.get(term.source)
            if existing is not None and existing.target != term.target:
                continue
            evidence = tuple(
                dict.fromkeys((existing.evidence_ids if existing else ()) + term.evidence_ids)
            )
            terms[term.source] = GlossaryTerm(
                source=term.source,
                target=term.target,
                evidence_ids=evidence,
                pinned=bool(existing and existing.pinned),
            )
    return ContextMemory(
        summary="；".join(dict.fromkeys(memory.summary for memory in memories if memory.summary)),
        speaker_style="；".join(
            dict.fromkeys(memory.speaker_style for memory in memories if memory.speaker_style)
        ),
        topics=tuple(
            dict.fromkeys(topic for memory in memories for topic in memory.topics if topic)
        ),
        terms=tuple(terms.values()),
    )


def baseline_context_memory(
    segments: tuple[Segment, ...],
    pinned_terms: tuple[GlossaryTerm, ...] = (),
) -> ContextMemory:
    seeds = builtin_glossary_for_segments(segments) + pinned_terms
    return _merge_memories([], seeds)


def analyze_context(
    segments: tuple[Segment, ...],
    *,
    provider: TranslationProvider,
    retries: int,
    pinned_terms: tuple[GlossaryTerm, ...] = (),
    max_characters: int = 12_000,
    overlap: int = 8,
) -> tuple[ContextMemory, tuple[dict[str, object], ...]]:
    if not segments:
        return ContextMemory(terms=pinned_terms), ()
    seed_terms = builtin_glossary_for_segments(segments) + pinned_terms
    chunks: list[tuple[Segment, ...]] = []
    start = 0
    while start < len(segments):
        end = start
        characters = 0
        while end < len(segments):
            next_characters = len(segments[end].text)
            if end > start and characters + next_characters > max_characters:
                break
            characters += next_characters
            end += 1
        chunks.append(segments[start:end])
        if end >= len(segments):
            break
        start = max(start + 1, end - overlap)

    memories: list[ContextMemory] = []
    metrics: list[dict[str, object]] = []
    for chunk_number, chunk in enumerate(chunks, 1):
        errors: list[str] = []
        for attempt in range(retries + 1):
            feedback = "" if not errors else f"\n上一次输出校验失败：{errors[-1]}"
            try:
                response = provider.generate(
                    system=_SYSTEM,
                    prompt=_prompt(chunk) + feedback,
                    schema=context_schema(tuple(segment.id for segment in chunk)),
                )
                raw_memory = json.loads(response.text)
                if not isinstance(raw_memory, dict):
                    raise TranslationError("语境响应根节点必须是 JSON 对象")
                raw_narrative = (
                    str(raw_memory.get("summary", "")),
                    str(raw_memory.get("speaker_style", "")),
                    *(str(value) for value in raw_memory.get("topics", [])),
                )
                sanitized_context_fields = sum(
                    not _valid_chinese_memory_text(value.strip())
                    for value in raw_narrative
                    if value.strip()
                )
                sanitized_memory = dict(raw_memory)
                sanitized_memory["summary"] = (
                    str(raw_memory.get("summary", "")).strip()
                    if _valid_chinese_memory_text(
                        str(raw_memory.get("summary", "")).strip()
                    )
                    else ""
                )
                sanitized_memory["speaker_style"] = (
                    str(raw_memory.get("speaker_style", "")).strip()
                    if _valid_chinese_memory_text(
                        str(raw_memory.get("speaker_style", "")).strip()
                    )
                    else ""
                )
                sanitized_memory["topics"] = [
                    str(value).strip()
                    for value in raw_memory.get("topics", [])
                    if str(value).strip()
                    and _valid_chinese_memory_text(str(value).strip())
                ]
                try:
                    memory = validate_context(sanitized_memory, chunk)
                    dropped_terms = 0
                except TranslationError:
                    if attempt < retries:
                        raise
                    memory = validate_context(
                        sanitized_memory,
                        chunk,
                        drop_invalid_terms=True,
                    )
                    dropped_terms = len(raw_memory.get("terms", [])) - len(memory.terms)
                memories.append(memory)
                metrics.append(
                    {
                        **response.metrics,
                        "stage": "context",
                        "chunk": chunk_number,
                        "attempts": attempt + 1,
                        "dropped_invalid_terms": dropped_terms,
                        "sanitized_context_fields": sanitized_context_fields,
                    }
                )
                break
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
        else:
            raise TranslationError(
                f"语境分析在 {retries + 1} 次尝试后失败: {' | '.join(errors)}"
            )
    return _merge_memories(memories, seed_terms), tuple(metrics)
