import pytest

from app.tools import repo_admin


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "mdk").mkdir()
    (root / "mdk" / "build.gradle").write_text("plugins {}", encoding="utf-8")
    (root / "note.txt").write_text("hello", encoding="utf-8")
    return root


def test_list_entries_directories_first(repo):
    entries = repo_admin.list_entries(str(repo))
    assert entries[0]["is_dir"] and entries[0]["name"] == "mdk"
    assert any(entry["name"] == "note.txt" and not entry["is_dir"]
               for entry in entries)


def test_save_file_into_subfolder(repo, tmp_path):
    source = tmp_path / "extra.zip"
    source.write_bytes(b"PK")
    message = repo_admin.save_file(str(repo), str(source), "mdk")
    assert "Сохранено" in message
    assert (repo / "mdk" / "extra.zip").exists()


def test_save_file_refuses_silent_overwrite(repo, tmp_path):
    source = tmp_path / "x.txt"
    source.write_text("a", encoding="utf-8")
    repo_admin.save_file(str(repo), str(source))
    with pytest.raises(repo_admin.RepoAdminError):
        repo_admin.save_file(str(repo), str(source))


def test_read_and_write_entry(repo):
    assert repo_admin.read_entry(str(repo), "note.txt") == "hello"
    repo_admin.write_entry(str(repo), "note.txt", "bye")
    assert (repo / "note.txt").read_text(encoding="utf-8") == "bye"


def test_archive_cannot_be_edited_as_text(repo):
    (repo / "mdk.zip").write_bytes(b"PK")
    with pytest.raises(repo_admin.RepoAdminError):
        repo_admin.read_entry(str(repo), "mdk.zip")


def test_rename_entry(repo):
    repo_admin.rename_entry(str(repo), "note.txt", "readme.txt")
    assert (repo / "readme.txt").exists()
    assert not (repo / "note.txt").exists()


def test_rename_rejects_separators(repo):
    with pytest.raises(repo_admin.RepoAdminError):
        repo_admin.rename_entry(str(repo), "note.txt", "../evil.txt")


def test_delete_moves_to_trash(repo):
    message = repo_admin.delete_entry(str(repo), "note.txt")
    assert "корзину" in message
    assert not (repo / "note.txt").exists()
    trashed = list((repo / "_trash").rglob("note.txt"))
    assert trashed and trashed[0].exists()


def test_delete_directory_to_trash(repo):
    repo_admin.delete_entry(str(repo), "mdk")
    assert not (repo / "mdk").exists()
    assert list((repo / "_trash").rglob("build.gradle"))


def test_create_dir(repo):
    repo_admin.create_dir(str(repo), "", "templates")
    assert (repo / "templates").is_dir()


def test_escape_is_refused(repo):
    with pytest.raises(repo_admin.RepoAdminError):
        repo_admin.list_entries(str(repo), "..")


def test_unconfigured_repository_is_reported():
    with pytest.raises(repo_admin.RepoAdminError):
        repo_admin.list_entries("")


def test_extract_dry_run_writes_nothing(repo, tmp_path):
    import zipfile

    archive = repo / "project.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("mdk/build.gradle", "plugins {}")
    project = tmp_path / "project"
    project.mkdir()

    plan = repo_admin.extract_to_project(str(repo), "project.zip", str(project), dry_run=True)
    assert "ПЛАН" in plan
    assert not (project / "build.gradle").exists()

    result = repo_admin.extract_to_project(str(repo), "project.zip", str(project))
    assert "РАСПАКОВАНО" in result
    assert (project / "build.gradle").read_text(encoding="utf-8") == "plugins {}"
