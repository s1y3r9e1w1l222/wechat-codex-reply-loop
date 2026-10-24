from contextlib import nullcontext
from dataclasses import replace
from wechat_autoreply.adapters.base import AdapterCapabilities, ScanResult


class FakeClock:
    def __init__(self, wall=1000.0):
        self.value, self.mono = wall, 0.0
    def wall(self):
        return self.value
    def monotonic(self):
        return self.mono
    def advance(self, seconds):
        self.value += seconds
        self.mono += seconds
    def jump_wall(self, seconds):
        self.value += seconds


class SyntheticAdapter:
    offline_demo = True
    def __init__(self, app_id, scans=(), chats=None):
        self.app_id, self.scans, self.chats = app_id, list(scans), chats or {}
        self.capabilities = AdapterCapabilities(app_id, True, app_id + '-synthetic', True)
        self.bridge, self.actions = None, []
        self.coverage = {'reached_end': True}
        self._scan = ScanResult((), True, self.coverage, 'synthetic', self.capabilities.revision)
    def scan(self, cancel=None, *, full=False):
        if self.scans:
            self._scan = self.scans.pop(0)
        return self._scan
    def read_chat(self, key, cancel=None):
        self.actions.append(('read', key))
        self.key = key
        return self.chats[key]
    def return_to_list(self, cancel=None):
        self.actions.append(('back',))
    def transaction(self, callback, cancel=None):
        return callback(self)
    def input_text(self, text):
        self.actions.append(('type', text))
        self.chats[self.key] = replace(self.chats[self.key], draft=text)
    def clear_own_draft(self, text):
        if self.chats[self.key].draft == text:
            self.chats[self.key] = replace(self.chats[self.key], draft='')
    def submit(self):
        from wechat_autoreply.models import Message
        snap = self.chats[self.key]
        self.actions.append(('submit', self.key))
        self.chats[self.key] = replace(snap, draft='', messages=snap.messages + [Message('outgoing', snap.draft)])
    def preview_requirements(self, config):
        return {self.capabilities.revision}
    def preview_evidence(self, scan):
        return {scan.adapter_token} if self.capabilities.verified else set()
