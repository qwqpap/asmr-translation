import json
from pathlib import Path


def test_quality_fixture_contains_thirty_classified_cases() -> None:
    path = Path(__file__).parent / "fixtures" / "context_quality_cases.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    expectations = [item for scene in data["scenes"] for item in scene["expectations"]]
    assert len(expectations) == 30
    assert {item["category"] for item in expectations} == {
        "asr_rooted",
        "translation_fixable",
    }
    assert sum(bool(item.get("critical")) for item in expectations) >= 4


def test_adult_fixture_covers_twenty_fidelity_cases() -> None:
    path = Path(__file__).parent / "fixtures" / "adult_translation_cases.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    assert len(data) >= 20
    assert len({item["id"] for item in data}) == len(data)
    assert all(item["ja"].strip() for item in data)
    assert any("同意" in item["ja"] for item in data)
    assert any("拒否" in item["ja"] for item in data)
    assert any("乳首" in item["ja"] for item in data)
    assert any("射精" in item["ja"] for item in data)
    assert any("仰向け" in item["ja"] for item in data)
    assert any("囁" in item["ja"] for item in data)
