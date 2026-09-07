from ngram.cognition import senses
from ngram.models import Input, is_group_like_chat, speaker_label_for_model


def test_private_telegram_not_group_like():
    inp = Input(
        text="hi",
        person_id="1",
        person_name="Pat",
        platform="telegram",
        metadata={"chat_type": "private"},
    )
    assert is_group_like_chat(inp) is False
    assert speaker_label_for_model(inp) == ""


def test_telegram_supergroup_gets_label():
    inp = Input(
        text="yo",
        person_id="99",
        person_name="Alex · @alex",
        platform="telegram",
        metadata={"chat_type": "supergroup"},
    )
    assert is_group_like_chat(inp) is True
    assert speaker_label_for_model(inp) == "[Alex · @alex · id 99] "


def test_discord_guild_label_includes_channel():
    inp = Input(
        text="hi",
        person_id="12",
        person_name="Sam",
        platform="discord",
        metadata={"chat_type": "guild", "channel_name": "general"},
    )
    assert is_group_like_chat(inp) is True
    assert speaker_label_for_model(inp) == "[Sam · id 12 · #general] "


def test_input_to_message_content_speaker_prefix():
    inp = Input(
        text="hello",
        person_id="1",
        person_name="Bo",
        platform="telegram",
        metadata={"chat_type": "group"},
    )
    out = senses.input_to_message_content(inp, 280, speaker_prefix=speaker_label_for_model(inp))
    assert out == "[Bo · id 1] hello"
