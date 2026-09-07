"""Gateway helper script resolution."""

from __future__ import annotations

import os

from ngram.utils import gateway_script


def test_gateway_script_path_points_at_existing_file() -> None:
    p = gateway_script.gateway_script_path()
    assert p.is_file()
    if os.name == "nt":
        assert p.name == "gateway.ps1"
    else:
        assert p.name == "gateway.sh"


def test_gateway_script_available_when_repo_layout_intact() -> None:
    assert gateway_script.gateway_script_available() is True
