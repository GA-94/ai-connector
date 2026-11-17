import os
import pytest
from app.tools.file_ops import read_file, write_file, edit_file

def test_write_and_read(tmp_path):
    root = str(tmp_path)
    path = os.path.join(root, "hello.txt")
    write_file(path, "Hello", root)
    assert read_file(path, root) == "Hello"

def test_write_outside_root_blocked(tmp_path):
    root = str(tmp_path)
    path = os.path.join(root, "..", "evil.txt")
    with pytest.raises(PermissionError):
        write_file(path, "x", root)

def test_blocked_extension(tmp_path):
    root = str(tmp_path)
    path = os.path.join(root, "bad.exe")
    with pytest.raises(PermissionError):
        write_file(path, "x", root)

def test_edit_file(tmp_path):
    root = str(tmp_path)
    path = os.path.join(root, "a.txt")
    write_file(path, "hello world", root)
    edit_file(path, "world", "there", root)
    assert read_file(path, root) == "hello there"

def test_edit_missing_text(tmp_path):
    root = str(tmp_path)
    path = os.path.join(root, "a.txt")
    write_file(path, "hello", root)
    with pytest.raises(ValueError):
        edit_file(path, "absent", "x", root)