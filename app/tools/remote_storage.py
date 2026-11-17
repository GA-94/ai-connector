"""Удалённые файловые хранилища: SFTP, SCP, FTP(S), SMB, Amazon S3, WebDAV.

Все протоколы приведены к одному набору операций (list / download / upload /
mkdir / rename / delete / stat), чтобы инструменты агента не зависели от
протокола, а пользователь не переучивался.

Зависимости подтягиваются по требованию (см. remote_common.need):
  SFTP / SCP   paramiko
  SMB          smbclient (smbprotocol)
  S3           boto3
  FTP          ftplib          — стандартная библиотека
  WebDAV       urllib.request  — стандартная библиотека
Поэтому на «чистой» системе и на ARM без колёс boto3/smbclient остальные
протоколы всё равно работают.
"""

import base64
import os
import posixpath
import ssl
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from app.tools.remote_common import (
    RemoteError, clean_path, join_remote, need, ssh_client,
)

CHUNK = 1024 * 256


# ------------------------------------------------------------------- базовый

class BaseStorage:
    """Общий интерфейс хранилища. Наследники реализуют операции."""

    protocol = "base"
    default_port = 0

    def __init__(self, profile: dict, credentials: dict, timeout: float = 20.0):
        self.profile = dict(profile or {})
        self.creds = dict(credentials or {})
        self.options = dict(self.profile.get("options") or {})
        try:
            saved = float(self.options.get("timeout") or self.profile.get("timeout") or 0)
        except (TypeError, ValueError):
            saved = 0.0
        self.timeout = float(saved or timeout or 20.0)
        self.root = str(self.options.get("root") or "").strip()

    # --- данные профиля
    @property
    def host(self) -> str:
        return str(self.profile.get("host") or "").strip()

    @property
    def port(self) -> int:
        return int(self.profile.get("port") or self.default_port or 0)

    @property
    def username(self) -> str:
        return str(self.profile.get("username") or "").strip()

    def opt(self, name: str, default=""):
        value = self.options.get(name, default)
        return default if value in (None, "") else value

    def flag(self, name: str, default: bool = False) -> bool:
        value = self.options.get(name)
        if value is None:
            return default
        return bool(value)

    def path_of(self, path: str) -> str:
        """Путь в хранилище с учётом корневой папки (идемпотентно).

        Важно: пути, полученные из list(), уже содержат корень. Повторное
        подставление корня ломало бы delete/rename после просмотра папки.
        """
        text = str(path or "").strip()
        root = str(self.root or "").strip().strip("/")
        normalized = text.replace("\\", "/")
        if root and (normalized == root or normalized.startswith(root + "/")):
            return clean_path(normalized)
        return join_remote(self.root, text)

    # --- жизненный цикл
    def open(self):
        return self

    def close(self):
        pass

    def __enter__(self):
        return self.open()

    def __exit__(self, *_exc):
        self.close()
        return False

    def describe(self) -> str:
        return f"{self.protocol}://{self.host}"

    # --- операции
    def list(self, path: str) -> list:
        raise NotImplementedError

    def download(self, remote: str, local: str) -> int:
        raise NotImplementedError

    def upload(self, local: str, remote: str) -> int:
        raise NotImplementedError

    def mkdir(self, path: str) -> None:
        raise NotImplementedError

    def rename(self, src: str, dst: str) -> None:
        raise NotImplementedError

    def delete(self, path: str, recursive: bool = False) -> None:
        raise NotImplementedError

    def stat(self, path: str) -> dict:
        raise NotImplementedError


# --------------------------------------------------------------------- SFTP

class SftpStorage(BaseStorage):
    protocol = "sftp"
    default_port = 22

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = None
        self.sftp = None

    def open(self):
        self.client = ssh_client(self.profile, self.creds, self.timeout)
        self.sftp = self.client.open_sftp()
        try:
            self.sftp.get_channel().settimeout(max(15, int(self.timeout)))
        except Exception:  # noqa: BLE001
            pass
        return self

    def close(self):
        for obj in (self.sftp, self.client):
            try:
                if obj is not None:
                    obj.close()
            except Exception:  # noqa: BLE001
                pass
        self.sftp = None
        self.client = None

    def list(self, path: str) -> list:
        target = self.path_of(path)
        try:
            attrs = self.sftp.listdir_attr(target)
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось прочитать {target}: {exc}") from exc
        rows = []
        for attr in attrs:
            is_dir = bool(attr.st_mode and (attr.st_mode & 0o040000))
            rows.append({
                "name": attr.filename,
                "path": posixpath.join(target, attr.filename),
                "dir": is_dir,
                "size": 0 if is_dir else int(attr.st_size or 0),
                "mtime": int(attr.st_mtime or 0),
            })
        rows.sort(key=lambda row: (not row["dir"], row["name"].lower()))
        return rows

    def download(self, remote: str, local: str) -> int:
        target = self.path_of(remote)
        try:
            self.sftp.get(target, local)
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось скачать {target}: {exc}") from exc
        return os.path.getsize(local) if os.path.exists(local) else 0

    def upload(self, local: str, remote: str) -> int:
        target = self.path_of(remote)
        folder = posixpath.dirname(target)
        if folder and folder != "/":
            self._mkdirs(folder)
        try:
            self.sftp.put(local, target)
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось загрузить {target}: {exc}") from exc
        return os.path.getsize(local) if os.path.exists(local) else 0

    def _mkdirs(self, path: str) -> None:
        parts, current = path.strip("/").split("/"), "/"
        for part in parts:
            if not part:
                continue
            current = posixpath.join(current, part)
            try:
                self.sftp.stat(current)
            except IOError:
                try:
                    self.sftp.mkdir(current)
                except Exception:  # noqa: BLE001 - мог создать кто-то другой
                    pass

    def mkdir(self, path: str) -> None:
        target = self.path_of(path)
        try:
            self.sftp.mkdir(target)
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось создать папку {target}: {exc}") from exc

    def rename(self, src: str, dst: str) -> None:
        source, target = self.path_of(src), self.path_of(dst)
        try:
            self.sftp.rename(source, target)
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось переименовать {source} -> {target}: {exc}") from exc

    def delete(self, path: str, recursive: bool = False) -> None:
        target = self.path_of(path)
        try:
            attr = self.sftp.stat(target)
            is_dir = bool(attr.st_mode and (attr.st_mode & 0o040000))
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не найдено: {target} ({exc})") from exc
        try:
            if not is_dir:
                self.sftp.remove(target)
                return
            if not recursive:
                self.sftp.rmdir(target)
                return
            for row in self.list(path):
                self.delete(row["path"], recursive=True)
            self.sftp.rmdir(target)
        except RemoteError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось удалить {target}: {exc}") from exc

    def stat(self, path: str) -> dict:
        target = self.path_of(path)
        try:
            attr = self.sftp.stat(target)
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не найдено: {target} ({exc})") from exc
        is_dir = bool(attr.st_mode and (attr.st_mode & 0o040000))
        return {"path": target, "dir": is_dir,
                "size": 0 if is_dir else int(attr.st_size or 0),
                "mtime": int(attr.st_mtime or 0)}


class ScpStorage(SftpStorage):
    """SCP идёт по тому же SSH-соединению; операции — через SFTP-подсистему.

    Так надёжнее, чем устаревший протокол scp(1): современные OpenSSH его
    отключают по умолчанию, а SFTP включён почти везде.
    """

    protocol = "scp"
    default_port = 22


# ---------------------------------------------------------------------- FTP

class FtpStorage(BaseStorage):
    protocol = "ftp"
    default_port = 21

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ftp = None

    def open(self):
        import ftplib

        host, port = self.host, self.port or 21
        if not host:
            raise RemoteError("в настройках подключения не задан адрес сервера (host)")
        password = str(self.creds.get("password") or "")
        user = self.username or "anonymous"
        try:
            if self.flag("tls", False):
                ftp = ftplib.FTP_TLS(timeout=self.timeout)
                ftp.connect(host, port)
                ftp.login(user, password)
                ftp.prot_p()
            else:
                ftp = ftplib.FTP(timeout=self.timeout)
                ftp.connect(host, port)
                ftp.login(user, password)
            ftp.set_pasv(not self.flag("active_mode", False))
            try:
                ftp.encoding = "utf-8"
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"FTP {host}:{port} — не удалось войти: {exc}") from exc
        self.ftp = ftp
        if self.root:
            try:
                ftp.cwd(self.root)
            except Exception as exc:  # noqa: BLE001
                raise RemoteError(f"нет папки {self.root} на FTP: {exc}") from exc
        return self

    def close(self):
        try:
            if self.ftp is not None:
                self.ftp.quit()
        except Exception:  # noqa: BLE001
            try:
                if self.ftp is not None:
                    self.ftp.close()
            except Exception:  # noqa: BLE001
                pass
        self.ftp = None

    def path_of(self, path: str) -> str:
        return str(path or ".").replace("\\", "/") or "."

    def list(self, path: str) -> list:
        target = self.path_of(path)
        rows = []
        try:
            for name, facts in self.ftp.mlsd(target, facts=["type", "size", "modify"]):
                if name in (".", ".."):
                    continue
                kind = str(facts.get("type") or "")
                rows.append({
                    "name": name,
                    "path": posixpath.join(target, name),
                    "dir": kind in ("dir", "cdir", "pdir"),
                    "size": int(facts.get("size") or 0),
                    "mtime": 0,
                })
        except Exception:
            rows = []
            try:
                names = self.ftp.nlst(target)
            except Exception as exc:  # noqa: BLE001
                raise RemoteError(f"не удалось прочитать {target}: {exc}") from exc
            for full in names:
                name = posixpath.basename(str(full).rstrip("/"))
                if not name or name in (".", ".."):
                    continue
                full_path = posixpath.join(target, name)
                is_dir = False
                size = 0
                try:
                    size = int(self.ftp.size(full_path) or 0)
                except Exception:  # noqa: BLE001 - size на папке падает
                    is_dir = True
                rows.append({"name": name, "path": full_path, "dir": is_dir,
                             "size": size, "mtime": 0})
        rows.sort(key=lambda row: (not row["dir"], row["name"].lower()))
        return rows

    def download(self, remote: str, local: str) -> int:
        target = self.path_of(remote)
        try:
            with open(local, "wb") as handle:
                self.ftp.retrbinary("RETR " + target, handle.write, CHUNK)
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось скачать {target}: {exc}") from exc
        return os.path.getsize(local)

    def upload(self, local: str, remote: str) -> int:
        target = self.path_of(remote)
        folder = posixpath.dirname(target)
        if folder and folder not in ("/", "."):
            self._mkdirs(folder)
        try:
            with open(local, "rb") as handle:
                self.ftp.storbinary("STOR " + target, handle, CHUNK)
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось загрузить {target}: {exc}") from exc
        return os.path.getsize(local)

    def _mkdirs(self, path: str) -> None:
        current = ""
        for part in path.strip("/").split("/"):
            if not part:
                continue
            current = (current + "/" + part) if current else part
            try:
                self.ftp.mkd(current)
            except Exception:  # noqa: BLE001 - уже существует
                pass

    def mkdir(self, path: str) -> None:
        try:
            self.ftp.mkd(self.path_of(path))
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось создать папку {path}: {exc}") from exc

    def rename(self, src: str, dst: str) -> None:
        try:
            self.ftp.rename(self.path_of(src), self.path_of(dst))
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось переименовать {src} -> {dst}: {exc}") from exc

    def delete(self, path: str, recursive: bool = False) -> None:
        target = self.path_of(path)
        if recursive:
            try:
                for row in self.list(target):
                    self.delete(row["path"], recursive=True)
            except RemoteError:
                pass
        try:
            self.ftp.delete(target)
        except Exception:
            try:
                self.ftp.rmd(target)
            except Exception as exc:  # noqa: BLE001
                raise RemoteError(f"не удалось удалить {target}: {exc}") from exc

    def stat(self, path: str) -> dict:
        target = self.path_of(path)
        size = 0
        is_dir = False
        try:
            size = int(self.ftp.size(target) or 0)
        except Exception:  # noqa: BLE001
            is_dir = True
        if is_dir:
            try:
                self.ftp.cwd(target)
                self.ftp.cwd(".." if not self.root else self.root)
            except Exception as exc:  # noqa: BLE001
                raise RemoteError(f"не найдено: {target} ({exc})") from exc
        return {"path": target, "dir": is_dir, "size": size, "mtime": 0}


# ---------------------------------------------------------------------- SMB

class SmbStorage(BaseStorage):
    protocol = "smb"
    default_port = 445

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mod = None
        self.unc_root = ""

    def open(self):
        self.mod = need("smbclient")
        host = self.host
        share = str(self.opt("share", "")).strip().strip("/\\")
        if not host or not share:
            raise RemoteError("для SMB нужны адрес сервера и общая папка (share) "
                              "в настройках подключения")
        if self.username:
            try:
                self.mod.register_session(host, username=self.username,
                                          password=str(self.creds.get("password") or ""))
            except Exception as exc:  # noqa: BLE001
                raise RemoteError(f"SMB {host} — не удалось войти: {exc}") from exc
        self.unc_root = "\\\\" + host + "\\" + share
        return self

    def close(self):
        host = self.host
        if self.mod is not None and host:
            try:
                self.mod.delete_session(host)
            except Exception:  # noqa: BLE001
                pass
        self.mod = None

    def path_of(self, path: str) -> str:
        rel = clean_path(path).strip("/")
        if rel in ("", "."):
            return self.unc_root
        folder = str(self.root or "").strip("/\\")
        if folder:
            rel = folder + "/" + rel
        return self.unc_root + "\\" + rel.replace("/", "\\")

    def list(self, path: str) -> list:
        target = self.path_of(path)
        rows = []
        try:
            for entry in self.mod.scandir(target):
                name = entry.name
                if name in (".", ".."):
                    continue
                is_dir = bool(getattr(entry, "is_dir", lambda: False)())
                rows.append({
                    "name": name,
                    "path": clean_path(posixpath.join(clean_path(path), name)),
                    "dir": is_dir,
                    "size": 0 if is_dir else int(getattr(entry, "st_size", 0) or 0),
                    "mtime": int(getattr(entry, "st_mtime", 0) or 0),
                })
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось прочитать {target}: {exc}") from exc
        rows.sort(key=lambda row: (not row["dir"], row["name"].lower()))
        return rows

    def download(self, remote: str, local: str) -> int:
        target = self.path_of(remote)
        try:
            with self.mod.open_file(target, mode="rb") as src, open(local, "wb") as dst:
                while True:
                    chunk = src.read(CHUNK)
                    if not chunk:
                        break
                    dst.write(chunk)
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось скачать {target}: {exc}") from exc
        return os.path.getsize(local)

    def upload(self, local: str, remote: str) -> int:
        target = self.path_of(remote)
        try:
            self.mod.makedirs(posixpath.dirname(target.replace("/", "\\")))
        except Exception:  # noqa: BLE001 - уже есть
            pass
        try:
            with open(local, "rb") as src, self.mod.open_file(target, mode="wb") as dst:
                while True:
                    chunk = src.read(CHUNK)
                    if not chunk:
                        break
                    dst.write(chunk)
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось загрузить {target}: {exc}") from exc
        return os.path.getsize(local)

    def mkdir(self, path: str) -> None:
        try:
            self.mod.makedirs(self.path_of(path))
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось создать папку {path}: {exc}") from exc

    def rename(self, src: str, dst: str) -> None:
        try:
            self.mod.rename(self.path_of(src), self.path_of(dst))
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось переименовать {src} -> {dst}: {exc}") from exc

    def delete(self, path: str, recursive: bool = False) -> None:
        target = self.path_of(path)
        if recursive:
            # smbclient умеет удалять дерево сам, но не во всех версиях —
            # поэтому сначала пробуем рекурсивный rmdir, а потом файл.
            try:
                self.mod.rmdir(target, recursive=True)
                return
            except Exception:  # noqa: BLE001
                pass
        try:
            self.mod.remove(target)
            return
        except Exception:  # noqa: BLE001
            pass
        try:
            self.mod.rmdir(target, recursive=bool(recursive))
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось удалить {target}: {exc}") from exc

    def stat(self, path: str) -> dict:
        target = self.path_of(path)
        try:
            info = self.mod.stat(target)
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не найдено: {target} ({exc})") from exc
        is_dir = bool(getattr(info, "st_file_attributes", 0) & 0x10)
        return {"path": target, "dir": is_dir,
                "size": 0 if is_dir else int(getattr(info, "st_size", 0) or 0),
                "mtime": int(getattr(info, "st_mtime", 0) or 0)}


# ----------------------------------------------------------------------- S3

class S3Storage(BaseStorage):
    protocol = "s3"
    default_port = 443

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = None

    def open(self):
        boto3 = need("boto3")
        access = str(self.creds.get("access_key") or self.username or "")
        secret = str(self.creds.get("secret_key") or self.creds.get("password") or "")
        if not access or not secret:
            raise RemoteError("для S3 нужны access key и secret key в настройках подключения")
        kwargs = dict(
            aws_access_key_id=access,
            aws_secret_access_key=secret,
            region_name=str(self.opt("region", "us-east-1")),
            config=_boto_config(boto3, self.timeout),
        )
        endpoint = str(self.opt("endpoint", "")).strip()
        if endpoint:
            kwargs["endpoint_url"] = endpoint
        self.client = boto3.client("s3", **kwargs)
        return self

    def close(self):
        self.client = None

    @property
    def bucket(self) -> str:
        value = str(self.opt("bucket", "")).strip()
        if not value:
            raise RemoteError("в настройках подключения S3 не задан bucket")
        return value

    def key_of(self, path: str) -> str:
        rel = clean_path(path).strip("/")
        prefix = str(self.root or "").strip("/")
        if prefix:
            if rel in ("", "."):
                return prefix
            if rel == prefix or rel.startswith(prefix + "/"):
                return rel          # ключ уже полный (пришёл из list)
            return prefix + "/" + rel
        return rel

    def list(self, path: str) -> list:
        prefix = self.key_of(path)
        marker = prefix + "/" if prefix and not prefix.endswith("/") else prefix
        try:
            page = self.client.list_objects_v2(Bucket=self.bucket, Prefix=marker, Delimiter="/")
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось прочитать s3://{self.bucket}/{marker}: {exc}") from exc
        rows = []
        for item in page.get("CommonPrefixes", []) or []:
            full = str(item.get("Prefix") or "")
            name = full[len(marker):].strip("/") or full.strip("/")
            rows.append({"name": name, "path": full, "dir": True, "size": 0, "mtime": 0})
        for item in page.get("Contents", []) or []:
            full = str(item.get("Key") or "")
            if full == marker:
                continue
            name = full[len(marker):] if full.startswith(marker) else full
            if not name or name.endswith("/"):
                continue
            rows.append({
                "name": name.strip("/"),
                "path": full,
                "dir": False,
                "size": int(item.get("Size") or 0),
                "mtime": int(item["LastModified"].timestamp()) if item.get("LastModified") else 0,
            })
        rows.sort(key=lambda row: (not row["dir"], row["name"].lower()))
        return rows

    def download(self, remote: str, local: str) -> int:
        key = self.key_of(remote)
        try:
            self.client.download_file(self.bucket, key, local)
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось скачать s3://{self.bucket}/{key}: {exc}") from exc
        return os.path.getsize(local)

    def upload(self, local: str, remote: str) -> int:
        key = self.key_of(remote)
        try:
            self.client.upload_file(local, self.bucket, key)
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось загрузить s3://{self.bucket}/{key}: {exc}") from exc
        return os.path.getsize(local)

    def mkdir(self, path: str) -> None:
        key = self.key_of(path).rstrip("/") + "/"
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=b"")
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось создать папку {key}: {exc}") from exc

    def rename(self, src: str, dst: str) -> None:
        source, target = self.key_of(src), self.key_of(dst)
        try:
            self.client.copy_object(Bucket=self.bucket, CopySource={
                "Bucket": self.bucket, "Key": source}, Key=target)
            self.client.delete_object(Bucket=self.bucket, Key=source)
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось переименовать {source} -> {target}: {exc}") from exc

    def delete(self, path: str, recursive: bool = False) -> None:
        key = self.key_of(path)
        try:
            if not recursive:
                self.client.delete_object(Bucket=self.bucket, Key=key)
                return
            prefix = key.rstrip("/") + "/"
            token = None
            while True:
                kwargs = dict(Bucket=self.bucket, Prefix=prefix)
                if token:
                    kwargs["ContinuationToken"] = token
                page = self.client.list_objects_v2(**kwargs)
                items = [{"Key": str(obj.get("Key"))} for obj in (page.get("Contents") or [])]
                if items:
                    self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": items})
                if not page.get("IsTruncated"):
                    break
                token = page.get("NextContinuationToken")
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"не удалось удалить s3://{self.bucket}/{key}: {exc}") from exc

    def stat(self, path: str) -> dict:
        key = self.key_of(path)
        try:
            head = self.client.head_object(Bucket=self.bucket, Key=key)
            return {"path": key, "dir": False, "size": int(head.get("ContentLength") or 0),
                    "mtime": int(head["LastModified"].timestamp()) if head.get("LastModified") else 0}
        except Exception:
            pass
        rows = self.list(path)
        if rows:
            return {"path": key, "dir": True, "size": 0, "mtime": 0}
        raise RemoteError(f"не найдено: s3://{self.bucket}/{key}")


def _boto_config(boto3, timeout: float):
    """Настройки таймаута boto3 без жёсткой зависимости от botocore.config."""
    try:
        from botocore.config import Config

        return Config(connect_timeout=max(3, int(timeout)), read_timeout=max(10, int(timeout * 3)),
                      retries={"max_attempts": 2})
    except Exception:  # noqa: BLE001
        return None


# -------------------------------------------------------------------- WebDAV

class WebDavStorage(BaseStorage):
    protocol = "webdav"
    default_port = 443

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.base = ""

    def open(self):
        host = self.host
        if not host:
            raise RemoteError("в настройках подключения не задан адрес сервера (host)")
        url = str(self.opt("url", "")).strip()
        if url:
            self.base = url
        else:
            scheme = str(self.opt("scheme", "https" if self.port in (443, 0) else "http"))
            self.base = f"{scheme}://{host}"
            if self.port and self.port not in (80, 443):
                self.base += f":{self.port}"
            self.base += "/" + str(self.opt("root", "")).strip("/")
        self.base = self.base.rstrip("/")
        return self

    # --- низкий уровень
    def _url(self, path: str) -> str:
        rel = clean_path(path).strip("/")
        url = self.base + ("/" + rel if rel and rel != "." else "")
        return urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%~")

    def _request(self, method: str, path: str, data=None, headers=None, stream_to=None):
        url = self._url(path)
        request = urllib.request.Request(url, data=data, method=method)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        token = str(self.creds.get("token") or "")
        if token:
            request.add_header("Authorization", "Bearer " + token)
        else:
            user = self.username
            password = str(self.creds.get("password") or "")
            if user or password:
                raw = f"{user}:{password}".encode("utf-8")
                request.add_header("Authorization", "Basic " + base64.b64encode(raw).decode("ascii"))
        context = None
        if url.lower().startswith("https"):
            context = ssl.create_default_context()
            if self.flag("insecure", False):
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout, context=context)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:200]
            except Exception:  # noqa: BLE001
                pass
            raise RemoteError(f"WebDAV {method} {url} -> HTTP {exc.code} {exc.reason} {body}") from exc
        except Exception as exc:  # noqa: BLE001
            raise RemoteError(f"WebDAV {method} {url} — {exc}") from exc
        if stream_to is not None:
            with response, open(stream_to, "wb") as handle:
                while True:
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    handle.write(chunk)
            return ""
        with response:
            return response.read().decode("utf-8", "replace")

    def list(self, path: str) -> list:
        body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:">'
                "<d:prop><d:resourcetype/><d:getcontentlength/><d:getlastmodified/>"
                "</d:prop></d:propfind>")
        url = self._url(path)
        raw = self._request("PROPFIND", path,
                            data=body.encode("utf-8"),
                            headers={"Depth": "1", "Content-Type": "application/xml"})
        rows = []
        try:
            tree = ET.fromstring(raw or "<empty/>")
        except ET.ParseError as exc:
            raise RemoteError(f"WebDAV вернул не XML: {exc}") from exc
        base_path = urllib.parse.urlparse(url).path.rstrip("/")
        for node in tree.iter():
            if not node.tag.endswith("response"):
                continue
            href, size, is_dir, name_hint = "", 0, False, ""
            for child in node.iter():
                tag = child.tag.split("}")[-1].lower()
                if tag == "href":
                    href = (child.text or "").strip()
                elif tag == "getcontentlength":
                    try:
                        size = int((child.text or "0").strip())
                    except ValueError:
                        size = 0
                elif tag == "resourcetype":
                    is_dir = any(item.tag.split("}")[-1].lower() == "collection"
                                 for item in child)
                elif tag == "displayname":
                    name_hint = (child.text or "").strip()
            if not href:
                continue
            path_part = urllib.parse.urlparse(href).path.rstrip("/")
            if not path_part or path_part == base_path:
                continue
            name = name_hint or posixpath.basename(urllib.parse.unquote(path_part))
            if not name:
                continue
            rel = name if not clean_path(path).strip("/") else \
                posixpath.join(clean_path(path), name)
            rows.append({"name": name, "path": rel, "dir": is_dir,
                         "size": 0 if is_dir else size, "mtime": 0})
        rows.sort(key=lambda row: (not row["dir"], row["name"].lower()))
        return rows

    def download(self, remote: str, local: str) -> int:
        self._request("GET", remote, stream_to=local)
        return os.path.getsize(local)

    def upload(self, local: str, remote: str) -> int:
        with open(local, "rb") as handle:
            data = handle.read()
        self._request("PUT", remote, data=data,
                      headers={"Content-Type": "application/octet-stream"})
        return len(data)

    def mkdir(self, path: str) -> None:
        self._request("MKCOL", path)

    def rename(self, src: str, dst: str) -> None:
        self._request("MOVE", src, headers={"Destination": self._url(dst), "Overwrite": "T"})

    def delete(self, path: str, recursive: bool = False) -> None:
        self._request("DELETE", path)

    def stat(self, path: str) -> dict:
        body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:">'
                "<d:prop><d:resourcetype/><d:getcontentlength/></d:prop></d:propfind>")
        raw = self._request("PROPFIND", path, data=body.encode("utf-8"),
                            headers={"Depth": "0", "Content-Type": "application/xml"})
        is_dir = "collection" in raw.lower()
        size = 0
        if not is_dir:
            import re as _re

            found = _re.search(r"<[^>]*getcontentlength[^>]*>\s*(\d+)", raw)
            size = int(found.group(1)) if found else 0
        return {"path": clean_path(path), "dir": is_dir, "size": size, "mtime": 0}


# ------------------------------------------------------------------ фабрика

_STORAGES = {
    "sftp": SftpStorage,
    "scp": ScpStorage,
    "ftp": FtpStorage,
    "smb": SmbStorage,
    "s3": S3Storage,
    "webdav": WebDavStorage,
}


def make_storage(profile: dict, credentials: dict, timeout: float = 20.0) -> BaseStorage:
    """Собрать бэкенд по протоколу профиля (без подключения)."""
    protocol = str((profile or {}).get("protocol") or "").strip().lower()
    cls = _STORAGES.get(protocol)
    if cls is None:
        raise RemoteError(f"протокол {protocol!r} не поддерживается. Доступны: "
                          + ", ".join(sorted(_STORAGES)))
    return cls(profile, credentials, timeout=timeout)


def supported_protocols() -> list:
    return sorted(_STORAGES)
