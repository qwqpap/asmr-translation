"""Relative links between the Markdown documents must resolve.

The docs cross-reference each other and the packaging assets a lot, and a moved
file or a renamed section is invisible until a reader hits a 404 on GitHub.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]

# Inline links only: [text](target).  Reference-style links are not used here.
_LINK = re.compile(r"\[[^\]]*?\]\((?P<target>[^)\s]+)\)")


def documents() -> list[Path]:
    found = [
        ROOT / "README.md",
        ROOT / "THIRD_PARTY_NOTICES.md",
        ROOT / "TODO.md",
        ROOT / "installer" / "README.md",
        ROOT / "packaging" / "linux" / "README.md",
        *sorted((ROOT / "docs").glob("*.md")),
    ]
    return [path for path in found if path.is_file()]


@pytest.mark.parametrize("document", documents(), ids=lambda path: path.name)
def test_relative_links_resolve(document: Path) -> None:
    text = document.read_text(encoding="utf-8")
    missing = []
    for match in _LINK.finditer(text):
        target = match["target"]
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = (document.parent / target.split("#", 1)[0]).resolve()
        if not path.exists():
            missing.append(target)
    assert not missing, f"{document.name}: {missing}"


def test_every_document_is_covered() -> None:
    # A new doc added under docs/ or packaging/ should not skip the link check.
    covered = {path.resolve() for path in documents()}
    for path in ROOT.glob("packaging/**/*.md"):
        assert path.resolve() in covered, path
