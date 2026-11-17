import pytest

from app.tools.safety import is_safe_path, is_blocked_extension, normalize_roots
from app.tools.workspace import Workspace, WorkspaceError
from app.tools import file_ops


def _make_project(base, name, files=()):
    root = base / name
    root.mkdir()
    for relative, text in files:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def test_safety_accepts_a_list_of_roots(tmp_path):
    a = _make_project(tmp_path, "alpha")
    b = _make_project(tmp_path, "beta")
    assert len(normalize_roots([str(a), str(b), str(a)])) == 2
    assert is_safe_path(str(a / "x.txt"), [str(a), str(b)]) is True
    assert is_safe_path(str(b / "x.txt"), [str(a), str(b)]) is True
    assert is_safe_path(str(tmp_path / "outside.txt"), [str(a), str(b)]) is False
    assert is_blocked_extension("gradlew.bat") is True


def test_relative_path_found_in_secondary_project(tmp_path):
    a = _make_project(tmp_path, "alpha")
    b = _make_project(tmp_path, "beta", [("only_b.txt", "B")])
    ws = Workspace(roots=[str(a), str(b)])
    assert ws.primary == a.resolve()
    assert ws.resolve("only_b.txt") == (b / "only_b.txt").resolve()
    assert ws.name_of(b / "only_b.txt") == "beta"


def test_explicit_project_and_prefix(tmp_path):
    a = _make_project(tmp_path, "alpha", [("shared.txt", "A")])
    b = _make_project(tmp_path, "beta", [("shared.txt", "B")])
    ws = Workspace(roots=[str(a), str(b)])
    # the same path exists in both projects: the primary wins
    assert ws.resolve("shared.txt") == (a / "shared.txt").resolve()
    # an explicit project wins over the primary
    assert ws.resolve("shared.txt", project="beta") == (b / "shared.txt").resolve()
    # the "name:/path" form works too
    assert ws.resolve("beta:shared.txt") == (b / "shared.txt").resolve()
    with pytest.raises(WorkspaceError):
        ws.resolve("shared.txt", project="gamma")


def test_nested_relative_path_with_project(tmp_path):
    a = _make_project(tmp_path, "alpha")
    b = _make_project(tmp_path, "beta", [("src/main/App.java", "class App {}")])
    ws = Workspace(roots=[str(a), str(b)])
    assert ws.resolve("src/main/App.java") == (b / "src/main/App.java").resolve()
    assert "class App {}" in file_ops.read_file("src/main/App.java", [str(a), str(b)])


def test_write_goes_to_primary_by_default(tmp_path):
    a = _make_project(tmp_path, "alpha")
    b = _make_project(tmp_path, "beta")
    ws = Workspace(roots=[str(a), str(b)])
    assert ws.resolve("new.txt", for_write=True) == (a / "new.txt").resolve()
    assert ws.resolve("new.txt", project="beta", for_write=True) == (b / "new.txt").resolve()


def test_outside_path_rejected(tmp_path):
    a = _make_project(tmp_path, "alpha")
    ws = Workspace(roots=[str(a)])
    with pytest.raises(WorkspaceError):
        ws.resolve(str(tmp_path / "evil.txt"), for_write=True)


def test_file_ops_work_with_a_list_of_roots(tmp_path):
    a = _make_project(tmp_path, "alpha")
    b = _make_project(tmp_path, "beta", [("readme.md", "hello")])
    roots = [str(a), str(b)]
    assert "hello" in file_ops.read_file("readme.md", roots)
    file_ops.write_file("created.txt", "data", roots)
    assert (a / "created.txt").read_text(encoding="utf-8") == "data"
    listing = file_ops.list_files(".", roots)
    assert "beta" in listing


def test_repository_is_read_only(tmp_path):
    a = _make_project(tmp_path, "alpha")
    repo = _make_project(tmp_path, "repo", [("tpl.txt", "template")])
    ws = Workspace(roots=[str(a)], repository=str(repo))
    assert ws.repository == repo.resolve()
    assert ws.resolve("repo:tpl.txt", allow_repo=True) == (repo / "tpl.txt").resolve()
    with pytest.raises(WorkspaceError):
        ws.resolve("repo:tpl.txt", allow_repo=True, for_write=True)
    with pytest.raises(WorkspaceError):
        ws.resolve("repo:tpl.txt")  # allow_repo is off by default


def test_describe_lists_projects_and_repository(tmp_path):
    a = _make_project(tmp_path, "alpha")
    repo = _make_project(tmp_path, "repo")
    ws = Workspace(roots=[str(a)], repository=str(repo))
    text = ws.describe()
    assert "alpha" in text
    assert "основной" in text
    assert "только чтение" in text
