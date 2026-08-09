import json

import pytest

from asmr_lrc.config import AppConfig
from asmr_lrc.errors import TranslationError
from asmr_lrc.models import Segment
from asmr_lrc.providers import ProviderConfig, ProviderResponse
from asmr_lrc.translation_context import (
    ContextMemory,
    GlossaryTerm,
    analyze_context,
    baseline_context_memory,
    context_schema,
    load_pinned_glossary,
    validate_context,
)


class ContextProvider:
    config = ProviderConfig("ollama", "http://local", "model")

    def __init__(self) -> None:
        self.calls = 0

    def check(self) -> None:
        return

    def generate(self, **_kwargs) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse(
            json.dumps(
                {
                    "summary": f"场景 {self.calls}",
                    "speaker_style": "轻声口语",
                    "topics": ["公园"],
                    "terms": [
                        {
                            "source": "松ぼっくり",
                            "target": "松果",
                            "evidence_ids": ["s2"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            {"provider": "fake"},
        )

    def unload(self) -> None:
        return


def test_context_schema_limits_evidence_ids() -> None:
    schema = context_schema(("s1", "s2"))
    evidence = schema["properties"]["terms"]["items"]["properties"]["evidence_ids"]
    assert evidence["items"]["enum"] == ["s1", "s2"]


def test_context_rejects_term_without_source_evidence() -> None:
    segments = (Segment("s1", 0, 1, "滑り台"),)
    with pytest.raises(TranslationError, match="没有原文证据"):
        validate_context(
            {
                "summary": "公园",
                "speaker_style": "口语",
                "topics": [],
                "terms": [
                    {"source": "松ぼっくり", "target": "松果", "evidence_ids": ["s1"]}
                ],
            },
            segments,
        )


def test_context_rejects_annotated_mixed_language_target() -> None:
    with pytest.raises(TranslationError, match="术语缺少原文"):
        validate_context(
            {
                "summary": "遥控器话题",
                "speaker_style": "口语",
                "topics": [],
                "terms": [
                    {
                        "source": "赤外線",
                        "target": "赤外線（红外线）",
                        "evidence_ids": ["s1"],
                    }
                ],
            },
            (Segment("s1", 0, 1, "赤外線の波長"),),
        )


def test_context_analysis_merges_pinned_terms() -> None:
    segments = (
        Segment("s1", 0, 1, "公園"),
        Segment("s2", 1, 2, "松ぼっくり"),
    )
    pinned = (GlossaryTerm("滑り台", "滑梯", (), True),)
    memory, metrics = analyze_context(
        segments,
        provider=ContextProvider(),
        retries=0,
        pinned_terms=pinned,
    )
    assert {(term.source, term.target) for term in memory.terms} == {
        ("滑り台", "滑梯"),
        ("松ぼっくり", "松果"),
    }
    assert metrics[0]["stage"] == "context"


def test_context_analysis_drops_invalid_term_after_finite_retry(monkeypatch) -> None:
    class InvalidTermProvider(ContextProvider):
        def generate(self, **_kwargs) -> ProviderResponse:
            self.calls += 1
            return ProviderResponse(
                json.dumps(
                    {
                        "summary": "公园",
                        "speaker_style": "轻声",
                        "topics": [],
                        "terms": [
                            {"source": "s1", "target": "滑梯", "evidence_ids": ["s1"]}
                        ],
                    },
                    ensure_ascii=False,
                ),
                {"provider": "fake"},
            )

    monkeypatch.setattr("asmr_lrc.translation_context.time.sleep", lambda _seconds: None)
    provider = InvalidTermProvider()

    memory, metrics = analyze_context(
        (Segment("s1", 0, 1, "滑り台"),),
        provider=provider,
        retries=1,
    )

    assert provider.calls == 2
    assert memory.terms == (GlossaryTerm("滑り台", "滑梯", ("s1",), True),)
    assert metrics[0]["dropped_invalid_terms"] == 1


def test_context_analysis_sanitizes_non_chinese_auxiliary_memory() -> None:
    class EnglishMemoryProvider(ContextProvider):
        def generate(self, **_kwargs) -> ProviderResponse:
            return ProviderResponse(
                json.dumps(
                    {
                        "summary": "A person discusses a TV remote",
                        "speaker_style": "casual storytelling",
                        "topics": ["remote malfunction"],
                        "terms": [],
                    }
                ),
                {"provider": "fake"},
            )

    memory, metrics = analyze_context(
        (Segment("s1", 0, 1, "リモコン"),),
        provider=EnglishMemoryProvider(),
        retries=0,
    )

    assert memory.summary == ""
    assert memory.speaker_style == ""
    assert memory.topics == ()
    assert metrics[0]["sanitized_context_fields"] == 3


def test_translation_profile_does_not_hash_api_key(tmp_path) -> None:
    first_provider = ProviderConfig(
        "openai", "https://example.test/v1", "model", api_key="first"
    )
    second_provider = ProviderConfig(
        "openai", "https://example.test/v1", "model", api_key="second"
    )
    first = AppConfig(
        cache_root=tmp_path,
        draft_provider=first_provider,
        review_provider=first_provider,
    )
    second = AppConfig(
        cache_root=tmp_path,
        draft_provider=second_provider,
        review_provider=second_provider,
    )
    assert first.translation_profile_id() == second.translation_profile_id()
    assert "first" not in first.translation_profile_id()


def test_context_memory_round_trip() -> None:
    memory = ContextMemory(
        "公园对话",
        "轻声",
        ("滑梯",),
        (GlossaryTerm("松ぼっくり", "松果", ("s1",)),),
    )
    assert ContextMemory.from_dict(memory.to_dict()) == memory


def test_builtin_glossary_requires_evidence_and_context_anchor() -> None:
    without_author = (
        Segment("s1", 0, 1, "学校でぽっちゃんって読んだ"),
        Segment("s2", 1, 2, "松ぼっくりを拾った"),
    )
    with_author = without_author + (Segment("s3", 2, 3, "夏目漱石の作品"),)

    first = baseline_context_memory(without_author)
    second = baseline_context_memory(with_author)

    assert {(term.source, term.target) for term in first.terms} == {
        ("松ぼっくり", "松果")
    }
    assert ("ぽっちゃん", "《少爷》") in {
        (term.source, term.target) for term in second.terms
    }
    assert all(term.evidence_ids for term in second.terms)


def test_builtin_glossary_preserves_speaker_self_reference() -> None:
    memory = baseline_context_memory(
        (Segment("s1", 0, 1, "お姉さんこう見えても家庭教師の資格持ってるから"),)
    )

    assert ("お姉さんこう見えても", "别看姐姐这样") in {
        (term.source, term.target) for term in memory.terms
    }


def test_user_pinned_glossary_overrides_builtin_term() -> None:
    memory = baseline_context_memory(
        (Segment("s1", 0, 1, "寒天を食べる"),),
        (GlossaryTerm("寒天", "寒天冻", (), True),),
    )

    assert memory.terms == (GlossaryTerm("寒天", "寒天冻", (), True),)


def test_load_pinned_glossary_for_shared_library(tmp_path) -> None:
    path = tmp_path / "固定术语.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "terms": [
                    {"source": "松ぼっくり", "target": "松果"},
                    {"source": "滑り台", "target": "滑梯"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    terms = load_pinned_glossary(path)

    assert [term.target for term in terms] == ["松果", "滑梯"]
    assert all(term.pinned for term in terms)


def test_load_pinned_glossary_rejects_conflicting_source(tmp_path) -> None:
    path = tmp_path / "glossary.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "terms": [
                    {"source": "坊っちゃん", "target": "少爷"},
                    {"source": "坊っちゃん", "target": "少主"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="冲突"):
        load_pinned_glossary(path)
