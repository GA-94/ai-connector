import zipfile

import pytest

from app.tools import repo_tools
from app.tools.workspace import Workspace

MDK_TOP = "forge-1.20.1-47.4.28-mdk"


def _zip(path, entries):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


@pytest.fixture()
def env(tmp_path):
    project = tmp_path / "mod"
    project.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    _zip(repo / "mdk.zip", {
        f"{MDK_TOP}/build.gradle": "plugins {}",
        f"{MDK_TOP}/gradle.properties": "minecraft_version=1.20.1",
        f"{MDK_TOP}/gradlew.bat": "echo hi",
        f"{MDK_TOP}/src/main/java/example/ExampleMod.java": "class ExampleMod {}",
        f"{MDK_TOP}/run/tool.exe": "MZ",
        f"{MDK_TOP}/../escaped.txt": "nope",
        "README.md": "loose root file",
    })
    ws = Workspace(roots=[str(project)], repository=str(repo))
    return ws, project, repo


def test_repo_list_marks_archives(env):
    ws, _, _ = env
    listing = repo_tools.repo_list(ws)
    assert "mdk.zip" in listing
    assert "[ARCHIVE]" in listing


def test_repo_archive_list_detects_top_folder(env):
    ws, _, _ = env
    text = repo_tools.repo_archive_list(ws, "mdk.zip")
    assert f"ОБЩИЙ ВЕРХНИЙ КАТАЛОГ: {MDK_TOP}/" in text
    assert "gradlew.bat" in text
    assert "tool.exe" in text


def test_dry_run_writes_nothing(env):
    ws, project, _ = env
    text = repo_tools.repo_extract(ws, "mdk.zip", dest=".", dry_run=True)
    assert "ПЛАН" in text
    assert not (project / "build.gradle").exists()
    assert not (project / "README.md").exists()


def test_extract_strips_top_folder(env):
    ws, project, _ = env
    text = repo_tools.repo_extract(ws, "mdk.zip", dest=".")
    assert "РАСПАКОВАНО" in text
    assert (project / "build.gradle").read_text(encoding="utf-8") == "plugins {}"
    assert (project / "gradle.properties").exists()
    assert (project / "src" / "main" / "java" / "example" / "ExampleMod.java").exists()
    # a .bat from a trusted MDK is extracted (and reported)
    assert (project / "gradlew.bat").exists()
    assert "gradlew.bat" in text
    # a loose root file must survive, not land inside a subfolder
    assert (project / "README.md").read_text(encoding="utf-8") == "loose root file"
    # executables and zip-slip entries are refused
    assert not (project / "run" / "tool.exe").exists()
    assert not (project.parent / "escaped.txt").exists()


def test_second_extract_skips_existing(env):
    ws, project, _ = env
    repo_tools.repo_extract(ws, "mdk.zip", dest=".")
    again = repo_tools.repo_extract(ws, "mdk.zip", dest=".")
    assert "ПРОПУЩЕНО" in again
    # and it does not explode when the file is already there
    assert (project / "build.gradle").exists()


def test_import_single_file_from_archive(env):
    ws, project, _ = env
    result = repo_tools.repo_import_file(
        ws, "mdk.zip", dest="src/main/java/example",
        member="src/main/java/example/ExampleMod.java")
    assert "Импортировано" in result
    assert (project / "src" / "main" / "java" / "example" / "ExampleMod.java").exists()


def test_read_member_without_top_folder(env):
    ws, _, _ = env
    text = repo_tools.repo_read_file(ws, "mdk.zip", member="build.gradle")
    assert "plugins {}" in text

    full = repo_tools.repo_read_file(ws, "mdk.zip", member=f"{MDK_TOP}/build.gradle")
    assert "plugins {}" in full


def test_unknown_member_reports_similar(env):
    ws, _, _ = env
    text = repo_tools.repo_read_file(ws, "mdk.zip", member="Build.gradle")
    assert "нет" in text
    assert "build.gradle" in text


def test_read_plain_repo_file(env):
    ws, _, _ = env
    # README.md лежит ВНУТРИ архива, а не в папке репозитория
    assert "loose root file" in repo_tools.repo_read_file(
        ws, "mdk.zip", member="README.md")


def test_missing_repository_is_reported(tmp_path):
    project = tmp_path / "mod"
    project.mkdir()
    ws = Workspace(roots=[str(project)])
    with pytest.raises(ValueError) as info:
        repo_tools.repo_list(ws)
    assert "не настроен" in str(info.value).lower()


def test_fetch_rejects_non_http(env):
    ws, _, _ = env
    assert "http" in repo_tools.repo_fetch(ws, "ftp://example.com/a.zip")
