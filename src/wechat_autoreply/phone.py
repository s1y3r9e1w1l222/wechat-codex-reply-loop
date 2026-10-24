"""Reuse the installed iPhone Use runtime, session and cross-process lock."""
import fcntl
import importlib
import json
import os
import re
import sys
import time
from pathlib import Path


class PhoneError(RuntimeError):
    def __init__(self, code, uncertain=False):
        super().__init__(code)
        self.code = code
        self.uncertain = uncertain


def discover_plugin(explicit=''):
    candidates = ([Path(explicit).expanduser()] if explicit else
                  sorted((Path.home() / '.codex/plugins/cache/iphone-use-local/iphone-use').glob('*'), reverse=True))
    for root in candidates:
        source = root / 'server/iphone_use.py'
        if not source.is_file():
            continue
        match = re.search(r'^VERSION\s*=\s*[\'\"]([^\'\"]+)', source.read_text(), re.M)
        if match and match.group(1) == '0.3.8':
            return root.resolve()
    raise PhoneError('未找到受支持的 iPhone Use 0.3.8，请配置 plugin_path')


class DeviceSession:
    """Valid only inside a PhoneBridge.transaction; never acquires the lock again."""
    def __init__(self, bridge, cancel, deadline):
        self.bridge, self.cancel, self.deadline = bridge, cancel, deadline

    def check(self):
        if self.cancel is not None and self.cancel.is_set():
            raise PhoneError('paused')
        if time.monotonic() >= self.deadline:
            raise PhoneError('transaction_timeout')
        screen = getattr(self.bridge.runtime, 'screen', None)
        if screen is not None and screen.paused():
            raise PhoneError('authentication_or_device_paused')

    def call(self, name, args):
        self.check()
        return self.bridge._invoke(self.bridge.runtime._call, name, args)

    def observe(self):
        return self.bridge.complete_observation(self.call('pua_observe', {'mode': 'tree', 'max_nodes': 500}))

    def focused_value(self):
        self.check()
        try:
            path, kind = self.bridge.runtime.phone.focused_field()
            return str(self.bridge.runtime.client.session('GET', path + '/attribute/value') or '')
        except Exception as e:
            raise PhoneError(getattr(e, 'code', 'focused_value_unavailable')) from e

    def clear_input(self, expected):
        self.check()
        runtime = self.bridge.runtime
        if runtime.client.request('GET', '/wda/locked').get('value') is not False:
            raise PhoneError('phone_locked')
        path, kind = runtime.phone.focused_field()
        if str(runtime.client.session('GET', path + '/attribute/value') or '') != expected:
            raise PhoneError('draft_changed_do_not_clear')
        runtime.phone.post(path + '/clear', {})
        if runtime.client.session('GET', path + '/attribute/value') not in ('', None):
            raise PhoneError('draft_clear_not_verified')


class PhoneBridge:
    def __init__(self, plugin_path='', runtime=None):
        self.compact = None
        self.plugin_path = None
        if runtime is None:
            self.plugin_path = discover_plugin(plugin_path)
            server = self.plugin_path / 'server'
            existing = sys.modules.get('iphone_use')
            if existing is not None and Path(existing.__file__).resolve().parent != server:
                raise PhoneError('存在其他路径的 iphone_use 模块，拒绝混用')
            sys.path.insert(0, str(server))
            module = importlib.import_module('iphone_use')
            if module.VERSION != '0.3.8':
                raise PhoneError('iPhone Use 版本不兼容')
            self.compact = importlib.import_module('wda_controller').compact_node
            runtime = module.Runtime()
        self.runtime = runtime
        # Large WeChat lists exceeded the upstream 15-second HTTP budget on the
        # tested device. This applies to this bridge only, not plugin settings.
        self.runtime.client.timeout = max(45, getattr(self.runtime.client, 'timeout', 15))

    @staticmethod
    def _invoke(method, name, args):
        try:
            return method(name, args)
        except PhoneError:
            raise
        except Exception as e:
            # Never include predicates, field values, credentials or chat content.
            raise PhoneError(getattr(e, 'code', 'phone_error'), getattr(e, 'uncertain', False)) from e

    def call(self, name, args=None):
        return self._invoke(self.runtime.call, name, args or {})

    def ready(self):
        status = self.call('pua_setup', {'action': 'status'})
        if not status.get('configured'):
            raise PhoneError('需要先完成 iPhone Use 配置')
        if not status.get('service', {}).get('ready'):
            active = next((j for j in status.get('jobs', []) if j.get('state') in ('running', 'queued')
                           and j.get('action') in ('start', 'recover')), None)
            if active:
                status = self.call('pua_setup', {'action': 'status', 'job_id': active['id'], 'wait_seconds': 20})
            else:
                status = self.call('pua_setup', {'action': 'start'})
            if not status.get('service', {}).get('ready'):
                raise PhoneError('服务仍在启动；保留当前工作后稍后恢复')
        ready = self.call('pua_ready', {'recover': True, 'screenshot': False})
        if not ready.get('ready'):
            raise PhoneError('服务未通过 READY，不能操作手机')
        return self.complete_observation(ready.get('observation', {}))

    def complete_observation(self, view):
        if not view.get('truncated'):
            return view
        snapshot = self.runtime.phone.snapshots.get(view.get('observation_id'), {})
        nodes = snapshot.get('nodes')
        if nodes is None or len(nodes) != view.get('total_nodes') or self.compact is None:
            return view
        # Same Runtime holds the complete parsed snapshot; do not invent coverage by
        # dropping the API's truncated flag without checking its original node count.
        return dict(view, nodes=[self.compact(n) for n in nodes],
                    truncated=False, api_was_truncated=True)

    def observe(self):
        return self.complete_observation(self.call('pua_observe', {'mode': 'tree', 'max_nodes': 500}))

    def transaction(self, callback, cancel=None, timeout=90):
        lock_path = self.runtime.state_dir / 'operation.lock'
        cache = self.runtime.state_dir / 'session.json'
        with lock_path.open('a') as lock:
            os.chmod(lock_path, 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise PhoneError('device_busy') from e
            try:
                if cache.is_file():
                    entry = json.loads(cache.read_text())
                    sid = entry.get('session_id', '')
                    if entry.get('url') == self.runtime.base_url and re.fullmatch(r'[A-Za-z0-9-]{1,128}', sid):
                        self.runtime.client.session_id = sid
                device = DeviceSession(self, cancel, time.monotonic() + timeout)
                device.check()
                return callback(device)
            finally:
                sid = self.runtime.client.session_id
                if sid:
                    tmp = cache.with_name('autoreply-session-' + str(os.getpid()) + '.tmp')
                    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(fd, 'w') as out:
                        json.dump({'url': self.runtime.base_url, 'session_id': sid}, out)
                    os.replace(tmp, cache)
                fcntl.flock(lock, fcntl.LOCK_UN)

    def close(self):
        client = getattr(self.runtime, 'client', None)
        if client is not None and hasattr(client, 'close'):
            client.close()

    def calibrate(self):
        from .wechat import Calibration, rect, text
        view = self.observe()
        viewport = view.get('viewport', {})
        size = tuple(viewport.get(k) for k in ('width', 'height'))
        # This geometry was inspected against the actual 440x956 device screenshot.
        return Calibration(list_bottom=868 if size == (440, 956) else 0,
                           viewport=size,
                           muted_marker_verified=any('消息免打扰' in text(n) or n.get('name') == 'bell_off_regular'
                                                     for n in view.get('nodes', [])),
                           list_region_complete=not view.get('truncated'), verified=False)
