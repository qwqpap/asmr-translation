import json

import pytest

from asmr_lrc.errors import TranslationError
from asmr_lrc.models import Segment
from asmr_lrc.translator import translate_batch, validate_translation


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
        {"translations": [{"id": "s1", "text": "```中文```"}]},
        {"translations": [{"id": "s2", "text": "你好"}]},
        {"translations": [{"id": "s1", "text": "你好"}, {"id": "s1", "text": "好"}]},
    ],
)
def test_translation_validation_rejects_invalid_output(data: object) -> None:
    with pytest.raises(TranslationError):
        validate_translation(data, ("s1",))


def test_translate_batch_retries_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        [
            {"response": "not json"},
            {
                "response": json.dumps(
                    {"translations": [{"id": "s1", "text": "你好"}]},
                    ensure_ascii=False,
                ),
                "total_duration": 1,
            },
        ]
    )

    monkeypatch.setattr("asmr_lrc.translator.ollama_request", lambda *_a, **_kw: next(responses))
    monkeypatch.setattr("asmr_lrc.translator.time.sleep", lambda _seconds: None)
    items, metrics = translate_batch(
        (Segment("s1", 0, 1, "こんにちは"),),
        base_url="http://local",
        model="model",
        retries=1,
        keep_alive="0",
    )
    assert items[0].text == "你好"
    assert metrics["attempts"] == 2
