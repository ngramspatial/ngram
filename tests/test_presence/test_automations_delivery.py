"""Defaults and fallbacks for automation delivery targets."""

from ngram.models import Input
from ngram.presence.tools.automations import (
    _default_deliver_from_ctx,
    _qualify_deliver_to_if_bare_id,
)
from ngram.presence.tools.runtime import ToolRuntimeContext


def test_default_deliver_from_ctx_telegram() -> None:
    ent = object()
    inp = Input(
        text="hi",
        person_id="1",
        person_name="U",
        channel="999",
        platform="telegram",
    )
    ctx = ToolRuntimeContext(entity=ent, inp=inp)
    assert _default_deliver_from_ctx(ctx) == "telegram:999"


def test_default_deliver_from_ctx_discord() -> None:
    ent = object()
    inp = Input(
        text="hi",
        person_id="1",
        person_name="U",
        channel="1234567890",
        platform="discord",
    )
    ctx = ToolRuntimeContext(entity=ent, inp=inp)
    assert _default_deliver_from_ctx(ctx) == "discord:1234567890"


def test_default_deliver_from_ctx_cli_empty() -> None:
    ent = object()
    inp = Input(
        text="hi",
        person_id="cli_user",
        person_name="You",
        channel="cli",
        platform="cli",
    )
    ctx = ToolRuntimeContext(entity=ent, inp=inp)
    assert _default_deliver_from_ctx(ctx) == ""


def test_default_deliver_no_inp() -> None:
    ctx = ToolRuntimeContext(entity=object(), inp=None)
    assert _default_deliver_from_ctx(ctx) == ""


def test_qualify_bare_id_telegram() -> None:
    inp = Input(text="x", person_id="1", person_name="U", channel="1", platform="telegram")
    ctx = ToolRuntimeContext(entity=object(), inp=inp)
    assert _qualify_deliver_to_if_bare_id("-1001234567890", ctx) == "telegram:-1001234567890"
    assert _qualify_deliver_to_if_bare_id("999", ctx) == "telegram:999"


def test_qualify_bare_id_discord() -> None:
    inp = Input(text="x", person_id="1", person_name="U", channel="x", platform="discord")
    ctx = ToolRuntimeContext(entity=object(), inp=inp)
    assert _qualify_deliver_to_if_bare_id("123456789012345678", ctx) == "discord:123456789012345678"


def test_qualify_already_qualified_unchanged() -> None:
    inp = Input(text="x", person_id="1", person_name="U", channel="1", platform="telegram")
    ctx = ToolRuntimeContext(entity=object(), inp=inp)
    assert _qualify_deliver_to_if_bare_id("telegram:555", ctx) == "telegram:555"


def test_qualify_bare_id_cli_no_change() -> None:
    inp = Input(text="x", person_id="u", person_name="You", channel="cli", platform="cli")
    ctx = ToolRuntimeContext(entity=object(), inp=inp)
    assert _qualify_deliver_to_if_bare_id("12345", ctx) == "12345"
