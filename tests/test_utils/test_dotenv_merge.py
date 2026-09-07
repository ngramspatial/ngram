from pathlib import Path

from ngram.utils.dotenv_merge import merge_dotenv_keys


def test_merge_dotenv_keys_creates_file(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    merge_dotenv_keys(p, {"A": "1", "B": "two"})
    text = p.read_text(encoding="utf-8")
    assert "A=1" in text
    assert "B=two" in text


def test_merge_dotenv_keys_replaces_and_preserves_comments(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("# header\nFOO=old\nBAR=keep\n", encoding="utf-8")
    merge_dotenv_keys(p, {"FOO": "new", "BAZ": "3"})
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert "# header" in lines
    assert "FOO=new" in lines
    assert "BAR=keep" in lines
    assert "BAZ=3" in lines


def test_merge_dotenv_keys_noop_on_empty_updates(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text("X=1\n", encoding="utf-8")
    merge_dotenv_keys(p, {})
    assert p.read_text(encoding="utf-8") == "X=1\n"
