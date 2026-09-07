"""Vendor reviewed static logos; never install or execute an upstream package."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import tarfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

VERSION = "1.95.0"
URL = f"https://registry.npmjs.org/@lobehub/icons-static-svg/-/icons-static-svg-{VERSION}.tgz"
INTEGRITY = "sha512-VSObF66DUVQe0EK3xbIoFcw+Fcia1+bLVFkMsQbmz0zCceMxPmoLImS5VM5SM7zL+/NarZ11/ySNfkekcomIkQ=="
ICONS = {
    "openai": "openai", "anthropic": "anthropic", "gemini": "gemini-color",
    "openrouter": "openrouter", "xai": "xai", "groq": "groq",
    "together": "together-color", "fireworks": "fireworks-color",
    "mistral": "mistral-color", "deepseek": "deepseek-color", "venice": "venice",
}
TAGS = {"svg", "g", "path", "rect", "circle", "ellipse", "polygon", "polyline",
        "line", "defs", "linearGradient", "radialGradient", "stop", "clipPath", "title"}
ATTRS = {"xmlns", "viewBox", "width", "height", "fill", "fill-rule", "clip-rule",
         "d", "id", "opacity", "fill-opacity", "stroke", "stroke-width", "stroke-linecap",
         "stroke-linejoin", "stroke-miterlimit", "stroke-opacity", "transform", "clip-path",
         "x", "y", "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry", "points",
         "offset", "stop-color", "stop-opacity", "gradientUnits", "gradientTransform"}


def validate_svg(raw: bytes) -> bytes:
    if len(raw) > 32_000 or re.search(br"<!DOCTYPE|<!ENTITY|<\?", raw, re.I):
        raise ValueError("Unexpected SVG payload")
    raw = raw.replace(b' style="flex:none;line-height:1"', b'')
    root = ET.fromstring(raw)
    if root.tag != "{http://www.w3.org/2000/svg}svg" or "viewBox" not in root.attrib:
        raise ValueError("SVG must have a viewBox")
    for node in root.iter():
        if node.tag.removeprefix("{http://www.w3.org/2000/svg}") not in TAGS:
            raise ValueError(f"Unexpected SVG element: {node.tag}")
        for key, value in node.attrib.items():
            if key not in ATTRS or re.search(r"(?:https?:|data:|javascript:|@import)", value, re.I):
                raise ValueError(f"Unexpected SVG attribute: {key}")
            if "url(" in value and not re.fullmatch(r"url\(#[\w.-]+\)", value):
                raise ValueError("Only local paint/clip references are allowed")
    # img documents do not inherit the host's currentColor. Use the mark's
    # monochrome artwork on a consistent light logo tile.
    return raw.replace(b"currentColor", b"#24252b")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    target = root / "ngramAR/packages/surface-webxr/public/providers"
    with urllib.request.urlopen(URL, timeout=30) as response:
        archive = response.read(8_000_001)
    digest = "sha512-" + base64.b64encode(hashlib.sha512(archive).digest()).decode()
    if digest != INTEGRITY:
        raise ValueError("Icon package integrity mismatch")
    assets = {}
    manifest = {"package": "@lobehub/icons-static-svg", "version": VERSION,
                "source": "https://github.com/lobehub/lobe-icons", "archive": URL,
                "integrity": INTEGRITY, "license": "MIT", "icons": {}}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
        for provider, slug in ICONS.items():
            name = f"package/icons/{slug}.svg"
            member = package.getmember(name)
            if not member.isfile() or member.size > 32_000:
                raise ValueError(f"Unexpected archive member: {name}")
            raw = package.extractfile(member).read()
            assets[provider] = validate_svg(raw)
            manifest["icons"][provider] = {
                "sourcePath": name,
                "originalSha256": hashlib.sha256(raw).hexdigest(),
                "sha256": hashlib.sha256(assets[provider]).hexdigest(),
            }
    target.mkdir(parents=True, exist_ok=True)
    for provider, raw in assets.items():
        (target / f"{provider}.svg").write_bytes(raw)
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Vendored {len(assets)} validated provider logos from {VERSION}.")


if __name__ == "__main__":
    main()
