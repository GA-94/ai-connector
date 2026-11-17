from app.tools import python_tools


def test_python_info_mentions_interpreter():
    info = python_tools.python_info()
    assert "PYTHON" in info
    assert "версия" in info


def test_run_inline_code(tmp_path):
    result = python_tools.python_run(root=str(tmp_path), code="print(40 + 2)")
    assert "42" in result
    assert "КОД ВОЗВРАТА: 0" in result


def test_run_script_from_project(tmp_path):
    (tmp_path / "hello.py").write_text("print('from script')", encoding="utf-8")
    result = python_tools.python_run(root=str(tmp_path), script="hello.py")
    assert "from script" in result


def test_requires_exactly_one_source(tmp_path):
    empty = python_tools.python_run(root=str(tmp_path))
    assert "одно" in empty.lower()
    both = python_tools.python_run(root=str(tmp_path), code="print(1)", script="x.py")
    assert "одно" in both.lower()


def test_disabled_by_setting(tmp_path):
    result = python_tools.python_run(root=str(tmp_path), code="print(1)", allow=False)
    assert "отключ" in result.lower()


def test_script_outside_project_is_refused(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("print(1)", encoding="utf-8")
    result = python_tools.python_run(root=str(project), script=str(outside))
    assert "вне проекта" in result


def test_exit_code_is_reported(tmp_path):
    result = python_tools.python_run(root=str(tmp_path), code="raise SystemExit(3)")
    assert "КОД ВОЗВРАТА: 3" in result


def test_working_directory_is_the_project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    result = python_tools.python_run(
        root=str(project), code="import os; print(os.getcwd())")
    assert project.name in result


def test_timeout_is_reported(tmp_path):
    result = python_tools.python_run(
        root=str(tmp_path), code="import time; time.sleep(5)", timeout_seconds=1)
    assert "TIMEOUT" in result
