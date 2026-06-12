"""
Device management: config persistence, connection detection, file transfer.
Supports: Cable (filesystem/MTP-GVFS), WiFi HTTP (CrossPoint/FileBrowser/BOOX Drop), SFTP/SSH.
"""

import getpass
import http.client
import json
import os
import shutil
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from folio.paths import CONFIG_DIR, DEVICES_CONFIG, EPUBS_DIR


def load_devices() -> list[dict]:
    if DEVICES_CONFIG.exists():
        try:
            return json.loads(DEVICES_CONFIG.read_text("utf-8")).get("devices", [])
        except Exception:
            pass
    return []


def save_devices(devices: list[dict]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DEVICES_CONFIG.write_text(
        json.dumps({"devices": devices}, ensure_ascii=False, indent=2), "utf-8"
    )


# ── Cable ─────────────────────────────────────────────────────────────────────

def _cable_path(device: dict) -> Path | None:
    p = device.get("path", "")
    if p:
        path = Path(p)
        if path.exists() and path.is_dir():
            return path
    return None


# ── WiFi HTTP ─────────────────────────────────────────────────────────────────

def _wifi_ip(device: dict) -> str:
    rest = device.get("path_wifi", "")[len("wifi://"):]
    slash = rest.find("/")
    return rest[:slash] if slash >= 0 else rest


def _wifi_folder(device: dict) -> str:
    rest = device.get("path_wifi", "")[len("wifi://"):]
    slash = rest.find("/")
    return rest[slash:] if slash >= 0 else "/"


def _is_wifi_connected(ip: str) -> bool:
    try:
        urllib.request.urlopen(f"http://{ip}/", timeout=2)
        return True
    except Exception:
        return False


def _wifi_detect_protocol(ip: str, user: str = "admin", passwd: str = "admin") -> tuple:
    """Returns ('crosspoint'|'filebrowser'|'booxdrop', token_or_None)."""
    try:
        data = json.dumps({"username": user, "password": passwd}).encode()
        req = urllib.request.Request(
            f"http://{ip}/api/login", data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            token = resp.read().decode().strip()
            if token:
                return ("filebrowser", token)
    except Exception:
        pass
    try:
        with urllib.request.urlopen(f"http://{ip}/api/device", timeout=3) as resp:
            if resp.status == 200:
                info = json.loads(resp.read().decode("utf-8", errors="replace"))
                if info.get("type") == "server":
                    return ("booxdrop", None)
    except Exception:
        pass
    try:
        with urllib.request.urlopen(f"http://{ip}/api/status", timeout=3) as resp:
            if resp.status == 200:
                return ("crosspoint", None)
    except Exception:
        pass
    return (None, None)


_UPLOAD_CHUNK = 65536


def _chunked_send(conn, data: bytes, on_progress=None):
    total = len(data)
    sent = 0
    while sent < total:
        chunk = data[sent:sent + _UPLOAD_CHUNK]
        conn.send(chunk)
        sent += len(chunk)
        if on_progress:
            on_progress(sent, total)


def _crosspoint_upload(epub_path: Path, ip: str, folder: str, on_progress=None) -> tuple:
    if not folder.startswith("/"):
        folder = "/" + folder
    boundary = uuid.uuid4().hex
    data = epub_path.read_bytes()
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{epub_path.name}"\r\n'
        f'Content-Type: application/epub+zip\r\n\r\n'
    ).encode() + data + f'\r\n--{boundary}--\r\n'.encode()
    try:
        conn = http.client.HTTPConnection(ip, timeout=120)
        conn.connect()
        conn.putrequest("POST", f"/upload?path={urllib.parse.quote(folder)}")
        conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        _chunked_send(conn, body, on_progress)
        resp = conn.getresponse()
        ok = resp.status == 200
        msg = resp.read().decode("utf-8", errors="replace")
        conn.close()
        return (ok, msg if not ok else "")
    except Exception as e:
        return (False, str(e))


def _filebrowser_upload(epub_path: Path, ip: str, folder: str, token: str, on_progress=None) -> tuple:
    data = epub_path.read_bytes()
    folder = folder.rstrip("/") or "/"
    path = f"/api/resources{folder}/{urllib.parse.quote(epub_path.name)}?override=true"
    try:
        conn = http.client.HTTPConnection(ip, timeout=120)
        conn.connect()
        conn.putrequest("POST", path)
        conn.putheader("X-Auth", token)
        conn.putheader("Content-Type", "application/octet-stream")
        conn.putheader("Content-Length", str(len(data)))
        conn.endheaders()
        _chunked_send(conn, data, on_progress)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="replace")
        conn.close()
        if resp.status in (200, 201, 204):
            return (True, "")
        return (False, f"HTTP {resp.status}: {body[:200]}")
    except Exception as e:
        return (False, str(e))


def _booxdrop_upload(epub_path: Path, ip: str, on_progress=None) -> tuple:
    data = epub_path.read_bytes()
    boundary = uuid.uuid4().hex
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{epub_path.name}"\r\n'
        f'Content-Type: application/epub+zip\r\n\r\n'
    ).encode() + data + f'\r\n--{boundary}--\r\n'.encode()
    try:
        conn = http.client.HTTPConnection(ip, timeout=120)
        conn.connect()
        conn.putrequest("POST", "/api/library/upload")
        conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        _chunked_send(conn, body, on_progress)
        resp = conn.getresponse()
        body_resp = resp.read().decode("utf-8", errors="replace")
        conn.close()
        if resp.status == 200:
            try:
                j = json.loads(body_resp)
                if j.get("successful") or j.get("code") == 0:
                    return (True, "")
            except Exception:
                return (True, "")
        return (False, f"HTTP {resp.status}: {body_resp[:200]}")
    except Exception as e:
        return (False, str(e))


def _wifi_upload(epub_path: Path, device: dict, on_progress=None) -> tuple:
    ip = _wifi_ip(device)
    folder = _wifi_folder(device)
    user = device.get("wifi_user", "admin")
    passwd = device.get("wifi_pass", "admin")
    proto, token = _wifi_detect_protocol(ip, user, passwd)
    if proto == "filebrowser":
        return _filebrowser_upload(epub_path, ip, folder, token, on_progress)
    if proto == "booxdrop":
        return _booxdrop_upload(epub_path, ip, on_progress)
    return _crosspoint_upload(epub_path, ip, folder, on_progress)


# ── SFTP/SSH ──────────────────────────────────────────────────────────────────

def _sftp_ip_port(device: dict) -> tuple:
    rest = device.get("path_sftp", "")[len("sftp://"):]
    slash = rest.find("/")
    host = rest[:slash] if slash >= 0 else rest
    if ":" in host:
        ip, port = host.rsplit(":", 1)
        return ip, int(port)
    return host, 2222


def _sftp_folder(device: dict) -> str:
    rest = device.get("path_sftp", "")[len("sftp://"):]
    slash = rest.find("/")
    return rest[slash:] if slash >= 0 else "/"


def _is_sftp_connected(ip: str, port: int, user: str, passwd: str) -> bool:
    try:
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(ip, port=port, username=user, password=passwd,
                       timeout=3, look_for_keys=False, allow_agent=False)
        client.close()
        return True
    except Exception:
        return False


def _sftp_upload(epub_path: Path, device: dict, on_progress=None) -> tuple:
    ip, port = _sftp_ip_port(device)
    folder = _sftp_folder(device)
    user = device.get("ssh_user", "root")
    passwd = device.get("ssh_pass", "root")
    try:
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(ip, port=port, username=user, password=passwd,
                       timeout=10, look_for_keys=False, allow_agent=False)
        sftp = client.open_sftp()
        remote_path = folder.rstrip("/") + "/" + epub_path.name
        def _cb(transferred, total):
            if on_progress and total > 0:
                on_progress(transferred, total)
        sftp.put(str(epub_path), remote_path, callback=_cb if on_progress else None)
        sftp.close()
        client.close()
        return (True, "")
    except Exception as e:
        return (False, str(e))


# ── Public API ────────────────────────────────────────────────────────────────

def is_connected(device: dict) -> bool:
    if _cable_path(device):
        return True
    if device.get("path_wifi"):
        ip = _wifi_ip(device)
        if ip and _is_wifi_connected(ip):
            return True
    if device.get("path_sftp"):
        ip, port = _sftp_ip_port(device)
        user = device.get("ssh_user", "root")
        passwd = device.get("ssh_pass", "root")
        if _is_sftp_connected(ip, port, user, passwd):
            return True
    return False


def connected_devices() -> list[dict]:
    return [d for d in load_devices() if is_connected(d)]


def detect_auto_paths() -> list[Path]:
    """Return candidate paths for connected e-readers (GVFS MTP + /run/media)."""
    found: list[Path] = []
    uid = os.getuid()
    gvfs = Path(f"/run/user/{uid}/gvfs")
    if gvfs.exists():
        for d in gvfs.iterdir():
            if d.name.startswith("mtp:") and d.is_dir():
                found.append(d)
    media = Path(f"/run/media/{getpass.getuser()}")
    if media.exists():
        for d in media.iterdir():
            if d.is_dir():
                found.append(d)
    return found


def send_book_to_device(epub_path: str, device: dict, on_progress=None) -> tuple[bool, str]:
    """
    Send a book to the device. Tries cable → WiFi → SFTP in order.
    epub_path is the absolute path stored in the database.
    Returns (success, message).
    """
    src = Path(epub_path)
    if not src.exists():
        return False, _("Source file not found: {path}").format(path=src)

    # Cable
    cable = _cable_path(device)
    if cable:
        try:
            dest_dir = cable / device.get("books_folder", "Books")
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest_dir / src.name)
            return True, _("Sent to {name}").format(name=device["name"])
        except Exception:
            pass  # fall through to WiFi

    # WiFi
    if device.get("path_wifi"):
        ip = _wifi_ip(device)
        if ip and _is_wifi_connected(ip):
            ok, msg = _wifi_upload(src, device, on_progress)
            if ok:
                return True, _("Sent to {name} via WiFi").format(name=device["name"])
            return False, msg

    # SFTP
    if device.get("path_sftp"):
        ip, port = _sftp_ip_port(device)
        user = device.get("ssh_user", "root")
        passwd = device.get("ssh_pass", "root")
        if _is_sftp_connected(ip, port, user, passwd):
            ok, msg = _sftp_upload(src, device, on_progress)
            if ok:
                return True, _("Sent to {name} via SFTP").format(name=device["name"])
            return False, msg

    return False, _("Device not connected.")


# ── Device file listing & download ────────────────────────────────────────────

_EBOOK_EXTS = {".epub", ".mobi", ".azw3", ".azw", ".pdf"}


def list_device_files(device: dict) -> tuple[list[dict], str | None]:
    """List ebook files on the device. Tries cable → WiFi → SFTP."""
    files: list[dict] = []
    err: str | None = None

    cable = _cable_path(device)
    if cable:
        try:
            for p in cable.iterdir():
                if p.suffix.lower() in _EBOOK_EXTS:
                    stat = p.stat()
                    files.append({
                        "name": p.name,
                        "size": stat.st_size,
                        "mtime": int(stat.st_mtime),
                        "path": str(p),
                        "proto": "cable",
                    })
            return files, None
        except Exception as e:
            err = str(e)

    if device.get("path_wifi"):
        ip = _wifi_ip(device)
        folder = _wifi_folder(device)
        user = device.get("wifi_user", "admin")
        passwd = device.get("wifi_pass", "admin")
        try:
            proto, token = _wifi_detect_protocol(ip, user, passwd)
        except Exception:
            proto, token = None, None

        if proto == "filebrowser" and token:
            try:
                folder_enc = urllib.parse.quote(folder.rstrip("/") or "/")
                req = urllib.request.Request(
                    f"http://{ip}/api/resources{folder_enc}/",
                    headers={"X-Auth": token},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8", errors="replace"))
                for item in data.get("items", []):
                    if item.get("isDir"):
                        continue
                    name = item.get("name", "")
                    if Path(name).suffix.lower() not in _EBOOK_EXTS:
                        continue
                    files.append({
                        "name": name,
                        "size": item.get("size", 0),
                        "mtime": 0,
                        "path": folder.rstrip("/") + "/" + name,
                        "proto": "filebrowser",
                        "ip": ip,
                        "token": token,
                    })
                return files, None
            except Exception as e:
                err = str(e)

        elif proto == "crosspoint":
            try:
                folder_enc = urllib.parse.quote(folder)
                url = f"http://{ip}/api/files?path={folder_enc}"
                with urllib.request.urlopen(url, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8", errors="replace"))
                for item in data if isinstance(data, list) else data.get("files", []):
                    name = item.get("name", "")
                    if Path(name).suffix.lower() not in _EBOOK_EXTS:
                        continue
                    files.append({
                        "name": name,
                        "size": item.get("size", 0),
                        "mtime": 0,
                        "path": folder.rstrip("/") + "/" + name,
                        "proto": "crosspoint",
                        "ip": ip,
                    })
                return files, None
            except Exception as e:
                err = str(e)

        elif proto == "booxdrop":
            try:
                with urllib.request.urlopen(f"http://{ip}/api/library", timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8", errors="replace"))
                for item in data.get("visibleBookList", []):
                    meta = item.get("metadata", {})
                    name = meta.get("title", "") or item.get("fileName", "")
                    book_id = meta.get("_id", "")
                    ext = Path(item.get("fileName", ".epub")).suffix.lower()
                    if ext not in _EBOOK_EXTS:
                        continue
                    files.append({
                        "name": name + ext if name else item.get("fileName", ""),
                        "size": item.get("fileSize", 0),
                        "mtime": 0,
                        "path": book_id,
                        "proto": "booxdrop",
                        "ip": ip,
                    })
                return files, None
            except Exception as e:
                err = str(e)

    if device.get("path_sftp"):
        ip, port = _sftp_ip_port(device)
        folder = _sftp_folder(device)
        user = device.get("ssh_user", "root")
        passwd = device.get("ssh_pass", "root")
        try:
            import paramiko
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(ip, port=port, username=user, password=passwd,
                           timeout=10, look_for_keys=False, allow_agent=False)
            sftp = client.open_sftp()
            for attr in sftp.listdir_attr(folder):
                name = attr.filename
                if Path(name).suffix.lower() not in _EBOOK_EXTS:
                    continue
                files.append({
                    "name": name,
                    "size": attr.st_size or 0,
                    "mtime": int(attr.st_mtime or 0),
                    "path": folder.rstrip("/") + "/" + name,
                    "proto": "sftp",
                    "ip": ip,
                    "port": port,
                    "user": user,
                    "passwd": passwd,
                })
            sftp.close()
            client.close()
            return files, None
        except Exception as e:
            err = str(e)

    return files, err


def delete_device_file(device: dict, file_info: dict) -> tuple[bool, str]:
    """Delete a file from the device according to its proto."""
    proto = file_info.get("proto", "cable")
    try:
        if proto == "cable":
            Path(file_info["path"]).unlink()
            return True, ""

        if proto == "filebrowser":
            ip = file_info.get("ip") or _wifi_ip(device)
            token = file_info.get("token", "")
            path_enc = urllib.parse.quote(file_info["path"])
            req = urllib.request.Request(
                f"http://{ip}/api/resources{path_enc}",
                method="DELETE",
                headers={"X-Auth": token},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status in (200, 204), ""

        if proto == "crosspoint":
            ip = file_info.get("ip") or _wifi_ip(device)
            path_enc = urllib.parse.quote(file_info["path"])
            req = urllib.request.Request(
                f"http://{ip}/api/files?path={path_enc}",
                method="DELETE",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200, ""

        if proto == "sftp":
            import paramiko
            ip = file_info.get("ip") or _sftp_ip_port(device)[0]
            port = file_info.get("port", 2222)
            user = file_info.get("user") or device.get("ssh_user", "root")
            passwd = file_info.get("passwd") or device.get("ssh_pass", "root")
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(ip, port=port, username=user, password=passwd,
                           timeout=10, look_for_keys=False, allow_agent=False)
            sftp = client.open_sftp()
            sftp.remove(file_info["path"])
            sftp.close()
            client.close()
            return True, ""

    except Exception as e:
        return False, str(e)

    return False, "Unsupported protocol"


def get_koreader_db_bytes(device: dict) -> tuple[bytes | None, str | None]:
    """Download KoReader statistics.sqlite3. Tries cable → WiFi filebrowser."""
    _CANDIDATES = [
        "koreader/settings/statistics.sqlite3",
        "koreader/settings/statistics.db",
        "koreader/statistics.sqlite3",
        ".adds/koreader/settings/statistics.sqlite3",
    ]

    cable = _cable_path(device)
    if cable:
        storage_root = cable.parent
        for rel in _CANDIDATES:
            p = storage_root / rel
            if p.exists():
                try:
                    return p.read_bytes(), None
                except Exception as e:
                    return None, str(e)

    if device.get("path_wifi"):
        ip = _wifi_ip(device)
        folder = _wifi_folder(device)
        user = device.get("wifi_user", "admin")
        passwd = device.get("wifi_pass", "admin")
        try:
            data = json.dumps({"username": user, "password": passwd}).encode()
            req = urllib.request.Request(
                f"http://{ip}/api/login", data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                token = resp.read().decode().strip()
        except Exception as e:
            return None, str(e)

        parent_folder = folder.rstrip("/").rsplit("/", 1)[0] or "/"
        for rel in _CANDIDATES:
            path = parent_folder.rstrip("/") + "/" + rel
            path_enc = urllib.parse.quote(path)
            try:
                dl_req = urllib.request.Request(
                    f"http://{ip}/api/raw{path_enc}",
                    headers={"X-Auth": token},
                )
                with urllib.request.urlopen(dl_req, timeout=15) as resp:
                    if resp.status == 200:
                        return resp.read(), None
            except Exception:
                continue

    return None, "KoReader database not found"


def download_device_file(file_info: dict) -> tuple[bytes | None, str | None]:
    """Download a single file from the device into memory."""
    proto = file_info.get("proto", "cable")
    try:
        if proto == "cable":
            return Path(file_info["path"]).read_bytes(), None

        if proto == "filebrowser":
            ip = file_info["ip"]
            token = file_info.get("token", "")
            path_enc = urllib.parse.quote(file_info["path"])
            req = urllib.request.Request(
                f"http://{ip}/api/raw{path_enc}",
                headers={"X-Auth": token},
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read(), None

        if proto == "sftp":
            import io as _io
            import paramiko
            ip = file_info.get("ip", "")
            port = file_info.get("port", 2222)
            user = file_info.get("user", "root")
            passwd = file_info.get("passwd", "root")
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(ip, port=port, username=user, password=passwd,
                           timeout=30, look_for_keys=False, allow_agent=False)
            sftp = client.open_sftp()
            buf = _io.BytesIO()
            sftp.getfo(file_info["path"], buf)
            sftp.close()
            client.close()
            return buf.getvalue(), None

    except Exception as e:
        return None, str(e)

    return None, "Unsupported protocol"
