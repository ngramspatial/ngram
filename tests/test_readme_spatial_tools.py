from pathlib import Path

from ngram.ngram_ar.spatial_tools import _SPATIAL_CAPABILITIES


def test_readme_documents_every_native_spatial_tool() -> None:
    readme = Path(__file__).parents[1] / "README.md"
    contents = readme.read_text(encoding="utf-8")

    assert f"receives {len(_SPATIAL_CAPABILITIES)} native spatial function" in contents

    missing = [
        tool_name
        for tool_name in _SPATIAL_CAPABILITIES
        if f"`{tool_name}`" not in contents
    ]

    assert missing == [], f"README is missing spatial tools: {missing}"
