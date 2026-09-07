"""Busy indicator HTML helper (Telegram harness UI, not model-facing)."""

import html as html_lib

from ngram.presence.platforms.telegram_platform import (
    _BUSY_BRAILLE,
    _BUSY_ING_WORDS,
    _BUSY_WORD_ROTATE_EVERY,
    _busy_indicator_html,
)


def test_busy_words_are_gerunds():
    assert all(w.endswith("ing") and " " not in w for w in _BUSY_ING_WORDS)


def test_busy_word_count_is_129():
    assert len(_BUSY_ING_WORDS) == 129


def test_busy_word_rotation_schedule():
    """Gerund changes every N spinner edits (see run_busy_indicator)."""
    assert _BUSY_WORD_ROTATE_EVERY == 20
    rotates = [
        f
        for f in range(1, 80)
        if f > 1 and (f - 1) % _BUSY_WORD_ROTATE_EVERY == 0
    ]
    assert rotates[:3] == [21, 41, 61]


def test_busy_indicator_is_html_code_monospace():
    t = _busy_indicator_html(0, "thinking")
    assert t.startswith("<code>")
    assert t.endswith("</code>")
    inner = html_lib.unescape(t[len("<code>") : -len("</code>")])
    assert _BUSY_BRAILLE[0] in inner
    assert "Thinking" in inner


def test_busy_indicator_only_spinner_changes_when_word_fixed():
    texts = {_busy_indicator_html(i, "reading") for i in range(30)}
    assert len(texts) == len(_BUSY_BRAILLE)
    for t in texts:
        assert "<code>" in t and "</code>" in t
        assert "Reading" in html_lib.unescape(
            t[len("<code>") : -len("</code>")]
        )
        assert "\n" not in t
        assert len(t) < 500
