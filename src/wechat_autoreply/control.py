"""Same-user, bounded local control. No phone calls run on this thread."""
import fcntl
import hashlib
import json
import os
import socket
import stat
import threading
from pathlib import Path

from .store import private_dir

MAX_REQUEST = 4096


def control_path(root):
    path = root / 'control.sock'
    if len(os.fsencode(path)) < 100:
        return path
    # macOS Unix sockets have a short path limit. Keep chat data in its chosen
    # directory and place only this endpoint in a same-user private short path.
    directory = private_dir(Path('/tmp') / ('wcrl-' + str(os.getuid())))
    digest = hashlib.sha256(os.fsencode(root)).hexdigest()[:32]
    return directory / (digest + '.sock')


class InstanceLease:
    def __init__(self, root, name='autoreply.service.lock'):
        root = private_dir(root)
        self.file = (root / name).open('a')
        os.chmod(root / name, 0o600)
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            self.file.close()
            raise RuntimeError('同一设备已有微信助手在运行') from e
    def close(self):
        if not self.file.closed:
            fcntl.flock(self.file, fcntl.LOCK_UN)
            self.file.close()


class ControlServer:
    def __init__(self, root, handler):
        self.root, self.handler = private_dir(root), handler
        self.path = control_path(self.root)
        self.closed = threading.Event()
        self.sock, self.thread = None, None
        self.bound = False

    def start(self):
        if self.path.exists():
            if not stat.S_ISSOCK(self.path.lstat().st_mode):
                raise ValueError('控制路径已有其他文件')
            # Never remove a live socket; stale socket can be removed under the lease.
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.connect(str(self.path))
            except (ConnectionRefusedError, FileNotFoundError):
                self.path.unlink(missing_ok=True)
            else:
                raise RuntimeError('控制服务已在运行')
            finally:
                probe.close()
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(self.path))
        self.bound = True
        os.chmod(self.path, 0o600)
        self.sock.listen(4)
        self.sock.settimeout(.2)
        self.thread = threading.Thread(target=self._run, daemon=True, name='wechat-control')
        self.thread.start()

    def _run(self):
        while not self.closed.is_set():
            try:
                client, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with client:
                client.settimeout(2)
                try:
                    data = bytearray()
                    while b'\n' not in data and len(data) <= MAX_REQUEST:
                        chunk = client.recv(min(1024, MAX_REQUEST + 1 - len(data)))
                        if not chunk:
                            break
                        data.extend(chunk)
                    if len(data) > MAX_REQUEST:
                        raise ValueError('request too large')
                    request = json.loads(data)
                    if not isinstance(request, dict) or not isinstance(request.get('command'), str):
                        raise ValueError('invalid request')
                    result = self.handler(request)
                except Exception:
                    result = {'ok':False, 'error':'invalid_control_request'}
                try:
                    client.sendall(json.dumps(result, ensure_ascii=False).encode() + b'\n')
                except OSError:
                    pass

    def close(self):
        self.closed.set()
        if self.sock:
            self.sock.close()
        if self.thread:
            self.thread.join(timeout=1)
        if self.bound and self.path.exists() and stat.S_ISSOCK(self.path.lstat().st_mode):
            self.path.unlink()
        self.bound = False


def send_command(root, request):
    root = private_dir(root)
    data = json.dumps(request, ensure_ascii=False).encode() + b'\n'
    if len(data) > MAX_REQUEST:
        raise ValueError('控制请求过大')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(str(control_path(root)))
        client.sendall(data)
        response = bytearray()
        while b'\n' not in response and len(response) <= 1024 * 1024:
            chunk = client.recv(16384)
            if not chunk:
                break
            response.extend(chunk)
        if len(response) > 1024 * 1024:
            raise ValueError('控制响应过大')
        return json.loads(response)
