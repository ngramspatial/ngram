"""The logo importer rejects active SVG content before it can enter the bundle."""
from pathlib import Path
import runpy

import pytest

validate_svg = runpy.run_path(str(Path(__file__).parents[1] / "scripts/vendor-provider-icons.py"))["validate_svg"]


@pytest.mark.parametrize("content", [
    '<script>alert(1)</script>', '<foreignObject/>',
    '<path onload="alert(1)"/>', '<image href="https://tracker.invalid/a"/>',
    '<path fill="url(https://tracker.invalid/a)"/>', '<path style="fill:red"/>',
    '<animate attributeName="href"/>',
])
def test_reject_active_svg(content):
    with pytest.raises(ValueError):
        validate_svg(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{content}</svg>'.encode())


def test_reject_entity_declarations():
    with pytest.raises(ValueError):
        validate_svg(b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg/>')
