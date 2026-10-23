"""Known-page navigation. Unknown identities and layouts stay in preview."""
from dataclasses import replace

from .phone import PhoneError
from .wechat import parse_chat, parse_list, rect, text


def nav_title(view):
    titles = [text(n) for n in view.get('nodes', []) if n.get('type') == 'NavigationBar']
    return titles[0] if len(titles) == 1 else ''


class WechatDriver:
    def __init__(self, bridge, calibration, max_pages=3, self_test=False):
        self.bridge, self.calibration = bridge, calibration
        self.max_pages, self.self_test = max_pages, self_test
        self.coverage = {'pages': 0, 'reached_end': False}

    def transaction(self, callback, cancel=None):
        return self.bridge.transaction(lambda device: callback(WechatSession(self, device)), cancel)

    def read_chat(self, key, cancel=None):
        return self.transaction(lambda session: session.read_chat(key), cancel)

    def return_to_list(self, cancel=None):
        return self.transaction(lambda session: session.return_to_list(), cancel)

    def scan(self, cancel=None):
        def scan_pages(session):
            def top():
                for _ in range(self.max_pages + 1):
                    current = session.device.observe()
                    session.require_list(current)
                    if session.at_list_top(current):
                        return current
                    session.device.call('pua_swipe', {'direction': 'down'})
                raise PhoneError('list_top_not_verified')

            def one_pass():
                view = top()
                merged, previous, signatures = [], [], []
                reached_end = False
                for page in range(self.max_pages):
                    rows = parse_list(view, self.calibration)
                    keys = [(r.key, r.label, r.preview, r.unread, r.unread_count,
                             r.muted, r.identity_verified) for r in rows]
                    if page:
                        if keys == previous:
                            reached_end = True
                            break
                        overlaps = [k for k in range(1, min(len(previous), len(keys)) + 1)
                                    if previous[-k:] == keys[:k]]
                        if len(overlaps) != 1:
                            raise PhoneError('list_changed_or_page_overlap_ambiguous')
                        rows = rows[overlaps[0]:]
                    signatures.append(keys)
                    offset = len(merged)
                    merged.extend(replace(r, position=float(offset + i)) for i, r in enumerate(rows))
                    previous = keys
                    if page + 1 == self.max_pages:
                        break
                    session.device.call('pua_swipe', {'direction': 'up', 'region': {
                        'x': 44, 'y': 210, 'width': 352, 'height': self.calibration.list_bottom - 240}})
                    view = session.device.observe()
                    session.require_list(view)
                return merged, signatures, reached_end

            first, signatures, reached_end = one_pass()
            second, repeated, repeated_end = one_pass()
            top()
            if signatures != repeated or reached_end != repeated_end:
                raise PhoneError('list_changed_between_passes')
            self.coverage = {'pages': len(signatures), 'reached_end': reached_end,
                             'scope': 'observed_pages_only', 'stable_passes': 2}
            return first
        return self.transaction(scan_pages, cancel)


class WechatSession:
    def __init__(self, driver, device):
        self.driver, self.device = driver, device
        self._identity = None

    def require_list(self, view):
        if not nav_title(view).startswith('微信'):
            raise PhoneError('expected_wechat_list')

    def at_list_top(self, view):
        return nav_title(view).startswith('微信') and any(n.get('type') == 'SearchField' and
                      95 <= rect(n)[1] < 200 for n in view.get('nodes', []))

    def _tap_node(self, node):
        x, y, w, h = rect(node)
        # Fresh coordinates avoid whole-app selector queries on very large lists.
        return self.device.call('pua_tap', {'x': x + w / 2, 'y': y + h / 2, 'observe': 'tree'})

    def _back(self, view):
        buttons = [n for n in view.get('nodes', []) if n.get('type') == 'Button'
                   and text(n).startswith('返回') and rect(n)[1] < 120]
        if len(buttons) != 1:
            raise PhoneError('back_button_ambiguous')
        self._tap_node(buttons[0])

    def return_to_list(self):
        view = self.device.observe()
        if nav_title(view).startswith('微信'):
            return
        if nav_title(view) not in self.driver.calibration.identities:
            raise PhoneError('unknown_page_do_not_navigate')
        self._back(view)
        view = self.device.observe()
        if nav_title(view).startswith('微信'):
            return
        # A known local-search result page can remain after returning from chat.
        if any(n.get('type') == 'TextView' for n in view.get('nodes', [])) and any(text(n) == '功能' for n in view.get('nodes', [])):
            self._back(view)
            self.require_list(self.device.observe())
            return
        raise PhoneError('did_not_return_to_list')

    def _open(self, key):
        cal = self.driver.calibration
        expected = next((name for name, value in cal.identities.items() if value == key), None)
        if expected is None:
            raise PhoneError('contact_not_calibrated')
        view = self.device.observe()
        if nav_title(view) == expected:
            return view
        self.require_list(view)
        for _ in range(self.driver.max_pages):
            rows = parse_list(view, cal)
            matches = [r for r in rows if r.key == key and r.identity_verified]
            if len(matches) == 1:
                x, y, w, h = matches[0].rect
                self.device.call('pua_tap', {'x': x + w / 2, 'y': y + h / 2})
                view = self.device.observe()
                if nav_title(view) != expected:
                    raise PhoneError('opened_wrong_chat')
                return view
            if len(matches) > 1:
                raise PhoneError('duplicate_contact_identity')
            self.device.call('pua_swipe', {'direction': 'up'})
            view = self.device.observe()
        raise PhoneError('contact_outside_scan')

    def _verify_profile(self, key, view):
        cal = self.driver.calibration
        if self.driver.self_test and key == 'filehelper' and nav_title(view) == '文件传输助手':
            return False, None
        probe = cal.identity_probes.get(key)
        signature = cal.profile_signatures.get(key)
        if not cal.verified or not probe or not signature:
            raise PhoneError('contact_profile_not_calibrated')
        muted, verified = None, False
        for selector in probe.get('steps', []):
            self.device.call('pua_tap', {'selector': selector})
            current = self.device.observe()
            verified = verified or any(text(n) == signature for n in current.get('nodes', []))
            switches = [n for n in current.get('nodes', []) if n.get('type') == 'Switch' and '消息免打扰' in text(n)]
            if len(switches) == 1:
                value = switches[0].get('value')
                muted = True if value in ('1', 1, True) else False if value in ('0', 0, False) else None
        for selector in probe.get('back_steps', []):
            self.device.call('pua_tap', {'selector': selector})
        if not verified or nav_title(self.device.observe()) != nav_title(view):
            raise PhoneError('profile_identity_not_verified')
        return muted, signature

    def read_chat(self, key):
        view = self._open(key)
        muted, signature = self._verify_profile(key, view)
        view = self.device.observe()
        voices = [n for n in view.get('nodes', []) if n.get('type') == 'Button' and text(n) == '语音'
                  and rect(n)[1] > 400]
        if len(voices) != 1:
            raise PhoneError('text_input_layout_unverified')
        _, y, _, h = rect(voices[0])
        self.device.call('pua_tap', {'x': 180, 'y': y + h / 2})
        draft = self.device.focused_value()
        snap = parse_chat(self.device.observe(), self.driver.calibration, key, muted, signature)
        snap.draft = draft
        return snap

    def input_text(self, reply):
        result = self.device.call('pua_type_text', {'text': reply, 'submit': False, 'verify': True})
        tokens = set()
        while result.get('input_complete') is False:
            token = result.get('continue_token')
            if not token or token in tokens or len(tokens) >= 16:
                raise PhoneError('invalid_input_continuation')
            tokens.add(token)
            result = self.device.call('pua_type_text', {'continue_token': token})
        if not result.get('exact_readback'):
            raise PhoneError('input_not_verified')

    def clear_own_draft(self, expected):
        self.device.clear_input(expected)

    def submit(self):
        view = self.device.observe()
        nodes = [n for n in view.get('nodes', []) if n.get('type') == 'Button'
                 and text(n) == '发送' and n.get('enabled', True)]
        keyboard = [n for n in nodes if n.get('name') == 'Send']
        if len(keyboard) == 1:
            nodes = keyboard
        if len(nodes) != 1:
            raise PhoneError('send_button_ambiguous')
        self._tap_node(nodes[0])
