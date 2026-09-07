"""Shell denylist must not false-positive on benign commands."""

from __future__ import annotations

import pytest

from ngram.config import HarnessConfig, entity_from_dict
from ngram.presence.tools.runtime import ToolRuntimeContext, reset_tool_runtime, set_tool_runtime
from ngram.presence.tools.shell import _blocked

_MIN = {
    "name": "ShellDeny",
    "personality": {
        "core_traits": {k: 0.5 for k in ["curiosity", "warmth", "assertiveness", "humor", "openness", "neuroticism", "conscientiousness"]},
        "behavioral_patterns": {},
        "voice": {},
        "backstory": "",
    },
    "drives": {"curiosity_topics": [], "attachment_threshold": 5, "restlessness_decay": 3600, "initiative_cooldown": 1800},
    "cognition": {},
    "presence": {"platforms": [], "daemon": {}},
}


class _H:
    __slots__ = ("config",)

    def __init__(self, c: object) -> None:
        self.config = c


@pytest.fixture
def _shell_ctx(tmp_path):
    h = HarnessConfig()
    h.memory.database_path = str(tmp_path / "m.db")
    ec = entity_from_dict(h, _MIN)
    tok = set_tool_runtime(ToolRuntimeContext(entity=_H(ec), inp=None))
    yield
    reset_tool_runtime(tok)


@pytest.mark.parametrize(
    "cmd",
    [
        'echo "information"',
        "ls ./User Information",
        "mv transformation notes ./x",
        "ren OldFolder NewFolder",
    ],
)
def test_shell_deny_does_not_match_information_substring(cmd: str, _shell_ctx) -> None:
    assert _blocked(cmd) is None


@pytest.mark.parametrize(
    "cmd",
    [
        "format c: /y",
        "FORMAT D:",
    ],
)
def test_shell_deny_blocks_windows_format_drive(cmd: str, _shell_ctx) -> None:
    assert _blocked(cmd) is not None
