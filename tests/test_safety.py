import os
from app.tools.safety import is_safe_path, is_blocked_extension

def test_safe_path_inside_root(tmp_path):
    root = str(tmp_path)
    target = os.path.join(root, "test.txt")
    assert is_safe_path(target, root) is True

def test_path_traversal_blocked(tmp_path):
    root = os.path.join(str(tmp_path), "workspace")
    os.makedirs(root)
    target = os.path.join(root, "..", "..", "Windows", "System32", "cmd.exe")
    assert is_safe_path(target, root) is False

def test_absolute_outside_root_blocked(tmp_path):
    root = str(tmp_path)
    assert is_safe_path("C:\\Windows\\System32\\cmd.exe", root) is False

def test_blocked_extension():
    assert is_blocked_extension("malware.exe") is True
    assert is_blocked_extension("script.bat") is True
    assert is_blocked_extension("normal.py") is False
    assert is_blocked_extension("readme.txt") is False