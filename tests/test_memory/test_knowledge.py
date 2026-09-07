"""Knowledge markdown parsing and section helpers."""

from ngram.memory.knowledge import (
    is_locked_title,
    parse_knowledge_sections,
    section_markdown,
)


def test_parse_h2_sections_skips_h3() -> None:
    raw = """## alpha
one

### not a split
still alpha

## beta
two
"""
    sections = parse_knowledge_sections(raw)
    assert [t for t, _ in sections] == ["alpha", "beta"]
    assert "not a split" in sections[0][1]
    assert sections[1][1].strip() == "two"


def test_locked_title() -> None:
    assert is_locked_title("[locked] x")
    assert is_locked_title("[LOCKED] y")
    assert not is_locked_title("open section")


def test_section_markdown_roundtrip_shape() -> None:
    s = section_markdown("music", "jazz")
    assert s == "## music\njazz"
    back = parse_knowledge_sections(s + "\n")
    assert back == [("music", "jazz")]
