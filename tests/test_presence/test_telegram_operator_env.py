"""Telegram environment allowlists merge with their YAML equivalents."""

from __future__ import annotations

import pytest

from ngram.presence.platforms.telegram_platform import (
    merge_telegram_allowed_user_ids,
    merge_telegram_operator_user_ids,
)


@pytest.mark.parametrize(
    ("yaml_ids", "env", "expect"),
    [
        ([], "", None),
        ([123], "", {123}),
        ([], "456", {456}),
        ([123], "456", {123, 456}),
        ([123], " 456 , 789 ", {123, 456, 789}),
        ([], "notanumber,42", {42}),
    ],
)
def test_merge_telegram_operator_user_ids(
    monkeypatch: pytest.MonkeyPatch,
    yaml_ids: list[int],
    env: str,
    expect: set[int] | None,
) -> None:
    monkeypatch.delenv("NGRAM_TELEGRAM_OPERATOR_IDS", raising=False)
    if env:
        monkeypatch.setenv("NGRAM_TELEGRAM_OPERATOR_IDS", env)
    got = merge_telegram_operator_user_ids(yaml_ids)
    assert got == expect


@pytest.mark.parametrize(
    ("yaml_ids", "env", "expect"),
    [
        ([], "", None),
        ([123], "", {123}),
        ([], "456", {456}),
        ([123], "456, 789", {123, 456, 789}),
        ([], "invalid,42", {42}),
    ],
)
def test_merge_telegram_allowed_user_ids(
    monkeypatch: pytest.MonkeyPatch,
    yaml_ids: list[int],
    env: str,
    expect: set[int] | None,
) -> None:
    monkeypatch.delenv("NGRAM_TELEGRAM_ALLOWED_USER_IDS", raising=False)
    if env:
        monkeypatch.setenv("NGRAM_TELEGRAM_ALLOWED_USER_IDS", env)
    got = merge_telegram_allowed_user_ids(yaml_ids)
    assert got == expect
