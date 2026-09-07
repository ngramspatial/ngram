from ngram.presence.tools.code_task import _normalize_task_path, _slug_objective


def test_slug_objective_basic() -> None:
    assert _slug_objective("Fix the API handler!") == "fix-the-api-handler"
    assert _slug_objective("") == "task"


def test_normalize_task_path_safe() -> None:
    assert _normalize_task_path("", slug="x").startswith("code_tasks/")
    assert _normalize_task_path("foo.md", slug="x") == "code_tasks/foo.md"
    assert _normalize_task_path("../etc/passwd", slug="x").startswith("code_tasks/")


def test_normalize_task_path_prefix() -> None:
    assert _normalize_task_path("code_tasks/manual.md", slug="x") == "code_tasks/manual.md"
