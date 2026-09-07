from __future__ import annotations

import re

from ngram.__version__ import __version__
from ngram.main import _app_version


def test_version_is_semver_triplet() -> None:
    assert re.match(r"^\d+\.\d+\.\d+$", __version__)


def test_app_version_matches_single_source() -> None:
    assert _app_version() == __version__
