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
