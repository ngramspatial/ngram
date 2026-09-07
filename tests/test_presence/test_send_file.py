from ngram.presence.tools.send_file import (
    _content_type_for_filename,
    _is_blocked_binary_payload,
)


def test_content_type_md_never_octet_stream() -> None:
    assert _content_type_for_filename("autonomy_transcript.md") == "text/markdown"
    assert _content_type_for_filename("FOO.MD") == "text/markdown"


def test_blocked_binary_unknown_ext() -> None:
    assert _is_blocked_binary_payload("application/octet-stream", "data.bin")
    assert _is_blocked_binary_payload("application/pdf", "x.pdf")


def test_not_blocked_for_known_text_ext_even_if_guess_would_fail() -> None:
    assert not _is_blocked_binary_payload("application/octet-stream", "notes.md")
