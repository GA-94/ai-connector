"""Временный тест S3-бэкенда через moto server (локальная эмуляция AWS S3)."""

import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.tools.remote_storage import make_storage

PORT = 9099
BUCKET = "aic-test-bucket"
PAYLOAD = b"ai-connector s3 self-test payload\n" * 3


def main():
    from moto.server import ThreadedMotoServer
    import boto3

    server = ThreadedMotoServer(port=PORT, verbose=False)
    server.start()
    try:
        cli = boto3.client(
            "s3", endpoint_url=f"http://127.0.0.1:{PORT}", region_name="us-east-1",
            aws_access_key_id="test", aws_secret_access_key="test")
        cli.create_bucket(Bucket=BUCKET)
        print("bucket создан:", BUCKET)

        profile = {"protocol": "s3", "host": "127.0.0.1", "username": "test",
                   "options": {"bucket": BUCKET, "endpoint": f"http://127.0.0.1:{PORT}",
                               "region": "us-east-1"}}
        creds = {"access_key": "test", "secret_key": "test"}

        tmpdir = tempfile.mkdtemp(prefix="aic_s3_")
        src = os.path.join(tmpdir, "upload.txt")
        dst = os.path.join(tmpdir, "download.txt")
        open(src, "wb").write(PAYLOAD)

        report = []
        try:
            with make_storage(profile, creds, timeout=15) as st:
                report.append(("connect", "OK " + st.describe() + " bucket=" + st.bucket))
                report.append(("list /", f"{len(st.list('/'))} объектов"))
                st.mkdir("aic_test_dir")
                report.append(("mkdir", "OK aic_test_dir"))
                st.upload(src, "aic_test_dir/upload.txt")
                report.append(("upload", "OK aic_test_dir/upload.txt"))
                info = st.stat("aic_test_dir/upload.txt")
                report.append(("stat", f"size={info.get('size')} dir={info.get('dir')}"))
                report.append(("list dir", ", ".join(str(r.get('name')) for r in st.list('aic_test_dir'))))
                st.rename("aic_test_dir/upload.txt", "aic_test_dir/renamed.txt")
                report.append(("rename", "OK -> renamed.txt"))
                size = st.download("aic_test_dir/renamed.txt", dst)
                data = open(dst, "rb").read()
                report.append(("download", f"{size} байт, совпадает={data == PAYLOAD}"))
                st.delete("aic_test_dir/renamed.txt")
                report.append(("delete file", "OK"))
                st.delete("aic_test_dir", recursive=True)
                report.append(("delete dir", "OK"))
        except Exception as exc:  # noqa: BLE001
            report.append(("FAIL", f"{type(exc).__name__}: {exc}"))
            traceback.print_exc()

        print("\n=== S3 (moto server) ===")
        for step, result in report:
            print(f"  [{step}] {result}")
        failed = [r for r in report if r[0] == "FAIL"]
        print("\nИТОГО S3:", "FAIL -> " + failed[0][1] if failed else "OK")
    finally:
        server.stop()


if __name__ == "__main__":
    main()
