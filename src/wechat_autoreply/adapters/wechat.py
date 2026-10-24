import hashlib
import json
import uuid

from .base import AdapterCapabilities, ScanResult


class WechatAdapter:
    app_id = 'wechat'

    def __init__(self, driver):
        self.driver = driver
        self.bridge = driver.bridge
        self.calibration = driver.calibration
        self.capabilities = AdapterCapabilities('wechat', bool(self.calibration.verified), 'wechat-v1', True)

    @property
    def coverage(self):
        return self.driver.coverage

    def scan(self, cancel=None, *, full=False):
        rows = self.driver.scan(cancel)
        coverage = dict(self.driver.coverage)
        return ScanResult(tuple(rows), bool(coverage.get('reached_end')), coverage,
                          uuid.uuid4().hex, self.capabilities.revision)

    def preview_requirements(self, config):
        cal = self.calibration
        result = set()
        for key in config.allowed:
            identity = {'signature': cal.profile_signatures.get(key),
                        'probe': cal.identity_probes.get(key),
                        'group': key in cal.group_keys,
                        'labels': [label for label, value in cal.identities.items() if value == key]}
            result.add(key + ':' + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest())
        return result

    def preview_evidence(self, scan):
        if not self.capabilities.verified:
            return set()
        keys = [row.key for row in scan.candidates if row.identity_verified]
        from types import SimpleNamespace
        return self.preview_requirements(SimpleNamespace(allowed=keys))

    def read_chat(self, key, cancel=None):
        return self.driver.read_chat(key, cancel)

    def return_to_list(self, cancel=None):
        return self.driver.return_to_list(cancel)

    def transaction(self, callback, cancel=None):
        return self.driver.transaction(callback, cancel)
