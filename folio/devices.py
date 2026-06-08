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


def send_book_to_device(book_epub_rel: str, device: dict) -> tuple[bool, str]:
    """
    Send a book to the device. Tries cable → WiFi → SFTP in order.
    book_epub_rel is the path relative to EPUBS_DIR stored in the database.
    Returns (success, message).
    """
    src = EPUBS_DIR / book_epub_rel
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
        except Exception as e:
            pass  # fall through to WiFi

    # WiFi
    if device.get("path_wifi"):
        ip = _wifi_ip(device)
        if ip and _is_wifi_connected(ip):
            ok, msg = _wifi_upload(src, device)
            if ok:
                return True, _("Sent to {name} via WiFi").format(name=device["name"])
            return False, msg

    # SFTP
    if device.get("path_sftp"):
        ip, port = _sftp_ip_port(device)
        user = device.get("ssh_user", "root")
        passwd = device.get("ssh_pass", "root")
        if _is_sftp_connected(ip, port, user, passwd):
            ok, msg = _sftp_upload(src, device)
            if ok:
                return True, _("Sent to {name} via SFTP").format(name=device["name"])
            return False, msg

    return False, _("Device not connected.")
