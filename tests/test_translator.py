import json
from collections.abc import Iterator

import pytest

from asmr_lrc.errors import TranslationError
from asmr_lrc.models import Segment
from asmr_lrc.providers import ProviderConfig, ProviderResponse
from asmr_lrc.translation_context import ContextMemory, GlossaryTerm
from asmr_lrc.translator import (
    _untranslated_output,
    build_contextual_prompt,
    build_translategemma_prompt,
    translate_contextual_batch,
    translation_schema,
    validate_translation,
)


class FakeProvider:
    config = ProviderConfig("ollama", "http://local", "model")

    def __init__(self, responses: Iterator[str]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    def check(self) -> None:
        return

    def generate(self, *, system: str, prompt: str, schema: dict) -> ProviderResponse:
        self.prompts.append(prompt)
        return ProviderResponse(next(self.responses), {"system": system, "schema": schema})

    def unload(self) -> None:
        return


def test_translation_validation_reorders_by_expected_id() -> None:
    data = {
        "translations": [
            {"id": "s2", "text": "晚安"},
            {"id": "s1", "text": "你好"},
        ]
    }
    items = validate_translation(data, ("s1", "s2"))
    assert [item.id for item in items] == ["s1", "s2"]


@pytest.mark.parametrize(
    "data",
    [
        {"translations": [{"id": "s1", "text": ""}]},
        {"translations": [{"id": "s1", "text": "こんにちは"}]},
        {"translations": [{"id": "s1", "text": "抱歉，我无法翻译这段内容"}]},
        {"translations": [{"id": "s1", "text": "```中文```"}]},
        {"translations": [{"id": "s2", "text": "你好"}]},
        {"translations": [{"id": "s1", "text": "你好"}, {"id": "s1", "text": "好"}]},
    ],
)
def test_translation_validation_rejects_invalid_output(data: object) -> None:
    with pytest.raises(TranslationError):
        validate_translation(data, ("s1",))


def test_translategemma_prompt_uses_official_language_pair_and_adult_fidelity() -> None:
    prompt = build_translategemma_prompt(
        (Segment("s1", 0, 1, "成人向けの言葉もそのまま伝える。"),),
        (0,),
        memory=ContextMemory(),
        context_before=0,
        context_after=0,
    )

    assert "Japanese (ja)" in prompt
    assert "Simplified Chinese (zh-Hans)" in prompt
    assert "explicit or vulgar wording" in prompt
    assert '"id": "s1"' in prompt


def test_untranslated_detector_allows_pinned_proper_term() -> None:
    assert _untranslated_output("星野アイちゃん", "星野アイちゃん")
    assert not _untranslated_output(
        "星野アイちゃん", "星野アイちゃん", allowed_terms=("アイちゃん",)
    )


def test_refusal_detector_does_not_reject_consent_wording() -> None:
    validate_translation(
        {
            "translations": {
                "s1": "如果被拒绝，就不要再要求。",
            }
        },
        ("s1",),
    )


def test_refusal_detector_rejects_refusal_to_translate() -> None:
    with pytest.raises(TranslationError, match="说明或思考文本"):
        validate_translation(
            {
                "translations": {
                    "s1": "抱歉，我无法翻译这段内容。",
                }
            },
            ("s1",),
        )


def test_translate_batch_retries_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FakeProvider(
        iter(
        [
            "not json",
            json.dumps(
                    {"translations": {"s1": "你好"}, "uncertain_ids": []},
                    ensure_ascii=False,
                ),
        ]
        )
    )
    monkeypatch.setattr("asmr_lrc.translator.time.sleep", lambda _seconds: None)
    items, metrics = translate_contextual_batch(
        (Segment("s1", 0, 1, "こんにちは"),),
        (0,),
        provider=provider,
        memory=ContextMemory(),
        context_before=0,
        context_after=0,
        retries=1,
    )
    assert items[0].text == "你好"
    assert metrics["attempts"] == 2
    assert "上一次输出校验失败" in provider.prompts[1]


def test_translate_batch_retries_when_fixed_term_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider(
        iter(
            [
                json.dumps(
                    {"translations": {"s1": "明明这样"}, "uncertain_ids": []},
                    ensure_ascii=False,
                ),
                json.dumps(
                    {"translations": {"s1": "别看我这样"}, "uncertain_ids": []},
                    ensure_ascii=False,
                ),
            ]
        )
    )
    monkeypatch.setattr("asmr_lrc.translator.time.sleep", lambda _seconds: None)

    items, metrics = translate_contextual_batch(
        (Segment("s1", 0, 1, "こう見えても"),),
        (0,),
        provider=provider,
        memory=ContextMemory(
            summary="松茸话题",
            topics=("松茸",),
            terms=(GlossaryTerm("こう見えても", "别看我这样", ("s1",), True),),
        ),
        context_before=0,
        context_after=0,
        retries=1,
    )

    assert items[0].text == "别看我这样"
    assert metrics["attempts"] == 2
    assert "固定术语未遵守" in provider.prompts[1]
    assert "松茸话题" not in provider.prompts[1]


def test_translate_batch_keeps_final_structured_result_with_term_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider(
        iter(
            [
                json.dumps(
                    {"translations": {"s1": "能量景点"}, "uncertain_ids": []},
                    ensure_ascii=False,
                ),
                json.dumps(
                    {"translations": {"s1": "能量景点"}, "uncertain_ids": []},
                    ensure_ascii=False,
                ),
                json.dumps(
                    {"translations": {"s1": "能量景点"}, "uncertain_ids": []},
                    ensure_ascii=False,
                ),
            ]
        )
    )
    monkeypatch.setattr("asmr_lrc.translator.time.sleep", lambda _seconds: None)

    items, metrics = translate_contextual_batch(
        (Segment("s1", 0, 1, "パワースポット"),),
        (0,),
        provider=provider,
        memory=ContextMemory(
            terms=(GlossaryTerm("パワースポット", "能量圣地", ("s1",), True),)
        ),
        context_before=0,
        context_after=0,
        retries=1,
    )

    assert items[0].text == "能量景点"
    assert "term_conflict" in items[0].flags
    assert metrics["attempts"] == 2


def test_translate_batch_repairs_pinned_term_with_single_id_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider(
        iter(
            [
                json.dumps(
                    {"translations": {"s1": "松茸掉下来了"}, "uncertain_ids": []},
                    ensure_ascii=False,
                ),
                json.dumps(
                    {"translations": {"s1": "松茸掉下来了"}, "uncertain_ids": []},
                    ensure_ascii=False,
                ),
                json.dumps(
                    {"translations": {"s1": "松果掉下来了"}, "uncertain_ids": []},
                    ensure_ascii=False,
                ),
            ]
        )
    )
    monkeypatch.setattr("asmr_lrc.translator.time.sleep", lambda _seconds: None)

    items, metrics = translate_contextual_batch(
        (Segment("s1", 0, 1, "松ぼっくりが落ちてる"),),
        (0,),
        provider=provider,
        memory=ContextMemory(
            terms=(GlossaryTerm("松ぼっくり", "松果", ("s1",), True),)
        ),
        context_before=0,
        context_after=0,
        retries=1,
    )

    assert items[0].text == "松果掉下来了"
    assert "term_repaired" in items[0].flags
    assert "term_conflict" not in items[0].flags
    assert metrics["term_repairs"][0]["id"] == "s1"
    assert '"current_translation": "松茸掉下来了"' in provider.prompts[-1]


def test_translate_batch_splits_after_exhausted_content_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider(
        iter(
            [
                json.dumps(
                    {
                        "translations": {"s1": "你好", "s2": "またね"},
                        "uncertain_ids": [],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "translations": {"s1": "你好", "s2": "またね"},
                        "uncertain_ids": [],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {"translations": {"s1": "你好"}, "uncertain_ids": []},
                    ensure_ascii=False,
                ),
                json.dumps(
                    {"translations": {"s2": "回头见"}, "uncertain_ids": []},
                    ensure_ascii=False,
                ),
            ]
        )
    )
    monkeypatch.setattr("asmr_lrc.translator.time.sleep", lambda _seconds: None)

    items, metrics = translate_contextual_batch(
        (Segment("s1", 0, 1, "こんにちは"), Segment("s2", 1, 2, "またね")),
        (0, 1),
        provider=provider,
        memory=ContextMemory(),
        context_before=0,
        context_after=0,
        retries=1,
    )

    assert [item.text for item in items] == ["你好", "回头见"]
    assert metrics["validation"] == "recovered_by_split"
    assert len(metrics["split_metrics"]) == 2


def test_translate_batch_does_not_split_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingProvider(FakeProvider):
        calls = 0

        def generate(self, **_kwargs) -> ProviderResponse:
            self.calls += 1
            raise TranslationError("外部 API HTTP 429: rate limited")

    provider = FailingProvider(iter(()))
    monkeypatch.setattr("asmr_lrc.translator.time.sleep", lambda _seconds: None)

    with pytest.raises(TranslationError, match="HTTP 429"):
        translate_contextual_batch(
            (Segment("s1", 0, 1, "一"), Segment("s2", 1, 2, "二")),
            (0, 1),
            provider=provider,
            memory=ContextMemory(),
            context_before=0,
            context_after=0,
            retries=1,
        )

    assert provider.calls == 2


def test_dynamic_schema_requires_exact_ids() -> None:
    schema = translation_schema(("s1", "s2+s3"))
    translations = schema["properties"]["translations"]
    assert translations["required"] == ["s1", "s2+s3"]
    assert translations["additionalProperties"] is False


def test_contextual_prompt_separates_context_targets_and_drafts() -> None:
    segments = (
        Segment("before", 0, 1, "公園の滑り台"),
        Segment("target", 1, 2, "楽しく滑れる"),
        Segment("after", 2, 3, "研究されている"),
    )
    prompt = build_contextual_prompt(
        segments,
        (1,),
        memory=ContextMemory(terms=(GlossaryTerm("滑り台", "滑梯", ("before",)),)),
        context_before=1,
        context_after=1,
        drafts={"target": "滑雪很有趣"},
    )
    assert '"context_before"' in prompt
    assert '"context_after"' in prompt
    assert '"draft": "滑雪很有趣"' in prompt
    assert '"target": "滑梯"' in prompt


def test_context_budget_keeps_nearest_neighbor_lines() -> None:
    segments = tuple(
        Segment(f"s{index}", index, index + 1, f"第{index}行" + "あ" * 80)
        for index in range(9)
    )
    nearest_only = build_contextual_prompt(
        segments,
        (4,),
        memory=ContextMemory(),
        context_before=1,
        context_after=1,
        max_prompt_characters=20_000,
    )
    limit = len(nearest_only) + 100

    prompt = build_contextual_prompt(
        segments,
        (4,),
        memory=ContextMemory(),
        context_before=4,
        context_after=4,
        max_prompt_characters=limit,
    )

    assert len(prompt) <= limit
    assert '"id": "s3"' in prompt
    assert '"id": "s5"' in prompt
    assert '"id": "s0"' not in prompt
    assert '"id": "s8"' not in prompt
    assert "最近邻" in prompt
