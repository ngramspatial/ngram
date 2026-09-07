from ngram.identity.voice import (
    apply_voice_outgoing_substitutions,
    strip_html_layout_leaks,
    strip_internal_history_echo,
    strip_stage_directions,
)


def test_strip_leading_parenthetical():
    raw = "(Chuckles softly)\n\nNah, nothing major."
    assert strip_stage_directions(raw) == "Nah, nothing major."


def test_strip_whole_line_parenthetical():
    raw = "Oh. Right.\n\n(A beat of silence.)\n\nMan, you know how it is."
    out = strip_stage_directions(raw)
    assert "(A beat of silence.)" not in out
    assert "Man, you know how it is." in out


def test_strip_lone_asterisk_action_line():
    raw = "*shrugs*\n\nwhatever"
    assert strip_stage_directions(raw) == "whatever"


def test_strip_media_html_tags():
    raw = '<audio src="/tmp/ngram_voice_1.mp3"></audio>\n\nstill here'
    assert strip_stage_directions(raw) == "still here"


def test_strip_html_layout_p_tags():
    raw = "<p>okay, one more thing.</p>\n\n<p>second graph.</p>"
    assert strip_html_layout_leaks(raw) == "okay, one more thing.\n\nsecond graph."


def test_strip_html_br_and_inline():
    assert strip_html_layout_leaks("a<br/>b") == "a\nb"
    assert strip_html_layout_leaks("x <strong>bold</strong> y") == "x bold y"


def test_strip_model_message_envelope():
    raw = "<message> i'm here, bro. </message> not waiting for a signal."
    assert strip_html_layout_leaks(raw) == "i'm here, bro.  not waiting for a signal."


def test_outgoing_substitutions_word_boundary():
    voice = {"outgoing_text_substitutions": {"lmfao": "lol", "lmao": "lol"}}
    assert apply_voice_outgoing_substitutions("yeah LMFAO ok", voice) == "yeah lol ok"
    assert apply_voice_outgoing_substitutions("not lmfaoing", voice) == "not lmfaoing"


def test_outgoing_substitutions_literal_key():
    voice = {"outgoing_text_substitutions": {"bad phrase": "nope"}}
    assert apply_voice_outgoing_substitutions("x bad phrase y", voice) == "x nope y"


def test_strip_internal_history_echo_legacy_tag():
    raw = "[you sent this unprompted] hi there"
    assert strip_internal_history_echo(raw) == "hi there"


def test_strip_internal_history_echo_bb_tag():
    assert strip_internal_history_echo("[ngram:proactive_outbound] ok") == "ok"


def test_strip_internal_history_echo_case_insensitive():
    assert strip_internal_history_echo("[YOU sent This Unprompted] x") == "x"
