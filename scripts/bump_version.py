#!/usr/bin/env python3
"""Bump ``ngram/__version__.py`` (patch, minor, or major).

Usage::

    python scripts/bump_version.py patch
    python scripts/bump_version.py minor
    python scripts/bump_version.py major

Then commit the change and ship.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_VERSION_FILE = _ROOT / "ngram" / "__version__.py"


def _parse_version(s: str) -> tuple[int, int, int]:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", s.strip())
    if not m:
        raise SystemExit(f"Expected semver like 1.2.3, got: {s!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _bump(v: tuple[int, int, int], part: str) -> tuple[int, int, int]:
    a, b, c = v
    if part == "major":
        return a + 1, 0, 0
    if part == "minor":
        return a, b + 1, 0
    return a, b, c + 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Bump ngram/__version__.py")
    parser.add_argument(
        "part",
        choices=("patch", "minor", "major"),
        help="Which segment to increment",
    )
    args = parser.parse_args()
    text = _VERSION_FILE.read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    if not m:
        raise SystemExit(f"No __version__ assignment in {_VERSION_FILE}")
    old = m.group(1)
    new = ".".join(str(x) for x in _bump(_parse_version(old), args.part))
    new_text = re.sub(
        r'__version__\s*=\s*["\'][^"\']+["\']',
        f'__version__ = "{new}"',
        text,
        count=1,
    )
    _VERSION_FILE.write_text(new_text, encoding="utf-8")
    print(f"ngram {old} -> {new} ({_VERSION_FILE})")


if __name__ == "__main__":
    main()
