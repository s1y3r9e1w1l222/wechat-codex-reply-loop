"""Known-page navigation. Unknown identities and layouts stay in preview."""
from dataclasses import replace
from collections import Counter

from .phone import PhoneError
from .wechat import is_list_title, matches_chat_title, parse_chat, parse_list, rect, text


def nav_title(view):
    titles = [text(n) for n in view.get('nodes', []) if n.get('type') == 'NavigationBar']
    return titles[0] if len(titles) == 1 else ''


class WechatDriver:
    def __init__(self, bridge, calibration, max_pages=3, self_test=False):
        self.bridge, self.calibration = bridge, calibration
        self.max_pages, self.self_test = max_pages, self_test
        self.coverage = {'pages': 0, 'reached_end': False}
        self.ambiguous_keys = set()
        self.scan_verified = False

    def transaction(self, callback, cancel=None, *, timeout=None):
        timeout = timeout if timeout is not None else max(240, self.max_pages * 120)
        return self.bridge.transaction(lambda device: callback(WechatSession(self, device)), cancel, timeout=timeout)

    def read_chat(self, key, cancel=None):
        return self.transaction(lambda session: session.read_chat(key), cancel)

    def return_to_list(self, cancel=None):
        return self.transaction(lambda session: session.return_to_list(), cancel)

    def scan(self, cancel=None):
        return self.transaction(self._scan_pages, cancel, timeout=max(240, self.max_pages * 120))

    def _scan_pages(self, session):
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
        counts = Counter(row.key for row in first)
        self.ambiguous_keys = {key for key, count in counts.items() if count > 1}
        first = [replace(row, identity_verified=False) if row.key in self.ambiguous_keys else row
                 for row in first]
        self.coverage = {'pages': len(signatures), 'reached_end': reached_end,
                         'scope': 'observed_pages_only', 'stable_passes': 2}
        self.scan_verified = True
        return first


class WechatSession:
    def __init__(self, driver, device):
        self.driver, self.device = driver, device
        self._identity = None

    def require_list(self, view):
        if not is_list_title(nav_title(view)):
            raise PhoneError('expected_wechat_list')

    def at_list_top(self, view):
        return is_list_title(nav_title(view)) and any(n.get('type') == 'SearchField' and
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
        if is_list_title(nav_title(view)):
            self._identity = None
            return
        if not any(matches_chat_title(nav_title(view), self.driver.calibration, key)
                   for key in self.driver.calibration.identities.values()):
            raise PhoneError('unknown_page_do_not_navigate')
        self._back(view)
        view = self.device.observe()
        if is_list_title(nav_title(view)):
            self._identity = None
            return
        # A known local-search result page can remain after returning from chat.
        if (any(n.get('type') == 'TextView' and 50 <= rect(n)[1] < 130 for n in view.get('nodes', []))
                and any(text(n) in ('功能', '最常使用') for n in view.get('nodes', []))):
            self._back(view)
            self.require_list(self.device.observe())
            self._identity = None
            return
        raise PhoneError('did_not_return_to_list')

    def _open(self, key):
        cal = self.driver.calibration
        expected = next((name for name, value in cal.identities.items() if value == key), None)
        if expected is None:
            raise PhoneError('contact_not_calibrated')
        if key in self.driver.ambiguous_keys:
            raise PhoneError('duplicate_contact_identity')
        if not cal.verified:
            raise PhoneError('list_identity_not_calibrated')
        view = self.device.observe()
        if matches_chat_title(nav_title(view), cal, key):
            if self._identity and self._identity[0] == key:
                return view
            # Recovery may start inside a chat. Re-establish its binding from the
            # list once; ordinary reads within this transaction stay in the chat.
            self.return_to_list()
            view = self.device.observe()
        self.require_list(view)
        if not self.driver.scan_verified:
            # Startup cleanup/reconciliation can run before the monitoring scan.
            # Use the same session and lock to establish uniqueness first.
            self.driver._scan_pages(self)
            view = self.device.observe()
            if key in self.driver.ambiguous_keys:
                raise PhoneError('duplicate_contact_identity')
        down, up = 0, 0
        while True:
            rows = parse_list(view, cal)
            matches = [r for r in rows if r.key == key and r.identity_verified]
            if len(matches) == 1:
                if matches[0].muted is not False:
                    raise PhoneError('muted_or_unknown')
                x, y, w, h = matches[0].rect
                self.device.call('pua_tap', {'x': x + w / 2, 'y': y + h / 2})
                view = self.device.observe()
                if not matches_chat_title(nav_title(view), cal, key):
                    raise PhoneError('opened_wrong_chat')
                self._identity = (key, matches[0].muted)
                return view
            if len(matches) > 1:
                raise PhoneError('duplicate_contact_identity')
            if not self.at_list_top(view) and up == 0:
                if down >= self.driver.max_pages + 1:
                    raise PhoneError('list_top_not_verified')
                direction = 'down'
                down += 1
            else:
                if up >= self.driver.max_pages - 1:
                    raise PhoneError('contact_outside_scan')
                direction = 'up'
                up += 1
            self.device.call('pua_swipe', {'direction': direction, 'region': {
                'x': 44, 'y': 210, 'width': 352, 'height': cal.list_bottom - 240}})
            view = self.device.observe()
            self.require_list(view)

    def read_chat(self, key):
        view = self._open(key)
        voices = [n for n in view.get('nodes', []) if n.get('type') == 'Button' and text(n) == '语音'
                  and rect(n)[1] > 400]
        if len(voices) != 1:
            raise PhoneError('text_input_layout_unverified')
        _, y, _, h = rect(voices[0])
        self.device.call('pua_tap', {'x': 180, 'y': y + h / 2})
        draft = self.device.focused_value()
        snap = parse_chat(self.device.observe(), self.driver.calibration, key,
                          self._identity[1], list_verified=True)
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

    def _send_control(self, view):
        if self._identity and not matches_chat_title(nav_title(view), self.driver.calibration, self._identity[0]):
            raise PhoneError('send_page_changed')
        nodes = [n for n in view.get('nodes', []) if n.get('type') in ('Button', 'Key')
                 and text(n).strip().casefold() in ('send', '发送') and n.get('enabled', True)]
        native = [n for n in nodes if n.get('name') == 'Send']
        if len(native) == 1:
            nodes = native
        if len(nodes) != 1:
            raise PhoneError('send_button_ambiguous')
        return nodes[0]

    def submit(self):
        node = self._send_control(self.device.observe())
        # iPhone Use resolves the element and invokes /element/<id>/click.
        # Never interpret third-party keyboard-relative rectangles as screen points.
        selector = {'type': node['type']}
        if node.get('label'):
            selector['label'] = node['label']
        if node.get('name'):
            selector['name'] = node['name']
        self.device.call('pua_tap', {'selector': selector, 'observe': 'tree'})
