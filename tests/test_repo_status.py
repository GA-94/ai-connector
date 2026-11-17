from app.tools.repo_status import check_repository


def test_empty_path_is_reported():
    status = check_repository("")
    assert not status.ok
    assert "не указана" in status.as_text()
    assert status.hints
    assert "не настроен" in status.short()


def test_missing_folder(tmp_path):
    status = check_repository(str(tmp_path / "nope"))
    assert not status.ok
    assert "не существует" in status.short()


def test_file_instead_of_directory(tmp_path):
    target = tmp_path / "repo.zip"
    target.write_bytes(b"PK")
    status = check_repository(str(target))
    assert not status.ok
    assert "файл" in status.short()


def test_ok_when_folder_has_archive(tmp_path):
    (tmp_path / "mdk.zip").write_bytes(b"PK")
    (tmp_path / "readme.txt").write_text("hi", encoding="utf-8")
    status = check_repository(str(tmp_path))
    assert status.ok
    assert status.archives == 1
    assert status.files == 2
    assert "OK" in status.short()
    assert status.exists and status.path


def test_empty_folder_warns(tmp_path):
    status = check_repository(str(tmp_path))
    assert status.ok
    assert any("пуста" in warning for warning in status.warnings)


def test_folder_without_archives_warns(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    status = check_repository(str(tmp_path))
    assert any("архив" in warning for warning in status.warnings)


def test_trash_folder_is_not_counted(tmp_path):
    trash = tmp_path / "_trash" / "20240101-000000"
    trash.mkdir(parents=True)
    (trash / "old.zip").write_bytes(b"PK")
    status = check_repository(str(tmp_path))
    assert status.archives == 0
    assert status.files == 0
