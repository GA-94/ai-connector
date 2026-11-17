"""Временный проверочный скрипт: гоняет app.tools.remote_storage по реальным
серверам на 129.101.93.45 (SSH/SFTP, FTP, WebDAV). Удалить после теста."""

import base64
import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.tools.remote_storage import make_storage, supported_protocols
from app.tools.remote_common import has_module

HOST = "129.101.93.45"
KEY_B64 = (
    "LS0tLS1CRUdJTiBPUEVOU1NIIFBSSVZBVEUgS0VZLS0tLS0KYjNCbGJuTnphQzFyWlhrdGRqRUFBQUFBQkc1dmJtVUFBQUFFYm05dVpRQUFBQUFBQUFBQkFBQUFNd0FBQUF0emMyZ3RaVwpReU5UVXhPUUFBQUNCY0dtOHVkZUVnWGxuV3k4VjhPR3RBVkR6dHNBd2I0OXFFaDNpSUFQQzE3UUFBQUtDTHZJZ09pN3lJCkRnQUFBQXR6YzJndFpXUXlOVFV4T1FBQUFDQmNHbTh1ZGVFZ1hsbld5OFY4T0d0QVZEenRzQXdiNDlxRWgzaUlBUEMxN1EKQUFBRUJNSTJxbDA0Z0NSOG12RFE5TFNXNm5rL2x6cVpFRGY3TFN3RCtkcHppTHUxd2FieTUxNFNCZVdkYkx4WHc0YTBCVQpQTzJ3REJ2ajJvU0hlSWdBOExYdEFBQUFHSEp2YjNSQVkzWTRNekkyTXpVM0xtNXZkbUZzYjJOaGJBRUNBd1FGCi0tLS0tRU5EIE9QRU5TU0ggUFJJVkFURSBLRVktLS0tLQo="
)
PRIVATE_KEY = base64.b64decode(KEY_B64).decode("utf-8")

PAYLOAD = b"ai-connector remote storage self-test payload\n" * 3


def run_case(name, profile, creds):
    print(f"\n=== {name} ===")
    report = []
    tmpdir = tempfile.mkdtemp(prefix="aic_remote_")
    local_src = os.path.join(tmpdir, "upload.txt")
    local_dst = os.path.join(tmpdir, "download.txt")
    with open(local_src, "wb") as fh:
        fh.write(PAYLOAD)

    storage = make_storage(profile, creds, timeout=15)
    try:
        with storage:
            report.append(("connect", "OK " + storage.describe()))
            rows = storage.list("/")
            report.append(("list /", f"{len(rows)} объектов, пример: "
                                     + ", ".join(str(r.get("name")) for r in rows[:4])))
            storage.mkdir("aic_test_dir")
            report.append(("mkdir", "OK aic_test_dir"))
            storage.upload(local_src, "aic_test_dir/upload.txt")
            report.append(("upload", "OK aic_test_dir/upload.txt"))
            st = storage.stat("aic_test_dir/upload.txt")
            report.append(("stat", f"size={st.get('size')} kind={st.get('type') or st.get('kind')}"))
            rows = storage.list("aic_test_dir")
            report.append(("list dir", ", ".join(str(r.get("name")) for r in rows)))
            storage.rename("aic_test_dir/upload.txt", "aic_test_dir/renamed.txt")
            report.append(("rename", "OK -> renamed.txt"))
            size = storage.download("aic_test_dir/renamed.txt", local_dst)
            got = open(local_dst, "rb").read()
            report.append(("download", f"{size} байт, совпадает={got == PAYLOAD}"))
            storage.delete("aic_test_dir/renamed.txt")
            report.append(("delete file", "OK"))
            storage.delete("aic_test_dir", recursive=True)
            report.append(("delete dir", "OK"))
    except Exception as exc:  # noqa: BLE001
        report.append(("FAIL", f"{type(exc).__name__}: {exc}"))
        traceback.print_exc()
    for step, result in report:
        print(f"  [{step}] {result}")
    return report


def main():
    print("протоколы в сборке:", supported_protocols())
    print("модули:", {m: has_module(m) for m in ("paramiko", "boto3", "smbclient")})

    cases = [
        ("SFTP (root@%s:22, ключ ed25519)" % HOST,
         {"protocol": "sftp", "host": HOST, "port": 22, "username": "root"},
         {"private_key": PRIVATE_KEY}),
        ("SCP (root@%s:22)" % HOST,
         {"protocol": "scp", "host": HOST, "port": 22, "username": "root"},
         {"private_key": PRIVATE_KEY}),
        ("FTP (anonymous@%s:2121)" % HOST,
         {"protocol": "ftp", "host": HOST, "port": 2121, "username": "anonymous"},
         {"password": "test@example.com"}),
        ("WebDAV (%s:8081, anonymous)" % HOST,
         {"protocol": "webdav", "host": HOST, "port": 8081, "username": "",
          "options": {"url": f"http://{HOST}:8081"}},
         {}),
    ]
    results = {}
    for name, profile, creds in cases:
        results[name] = run_case(name, profile, creds)
    print("\n=== ИТОГО ===")
    for name, report in results.items():
        failed = [r for r in report if r[0] == "FAIL"]
        print(f"  {'FAIL' if failed else 'OK  '} {name}" + (f" -> {failed[0][1]}" if failed else ""))


if __name__ == "__main__":
    main()
