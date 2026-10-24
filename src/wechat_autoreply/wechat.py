"""Fail-closed parsing for a calibrated Chinese WeChat accessibility layout."""
import re
from collections import Counter
from dataclasses import dataclass, field

from .models import ChatSnapshot, ConversationCandidate, Message

SERVICE_NAMES = {'公众号', '服务号', '服务通知', '微信支付', '微信团队', '订阅号消息', '文件传输助手'}


@dataclass
class Calibration:
    list_bottom: float = 0
    muted_marker_verified: bool = False
    list_region_complete: bool = False
    identities: dict = field(default_factory=dict)
    profile_signatures: dict = field(default_factory=dict)
    identity_probes: dict = field(default_factory=dict)
    group_keys: list = field(default_factory=list)
    bundle_id: str = 'com.tencent.xin'
    viewport: tuple = (440, 956)
    verified: bool = False


def text(n):
    return n.get('label') or n.get('name') or ''


def rect(n):
    r = n.get('rect', [0, 0, 0, 0])
    if isinstance(r, dict):
        return tuple(r.get(k, 0) for k in ('x', 'y', 'width', 'height'))
    return tuple(r)


def is_list_title(title):
    return bool(re.fullmatch(r'微信(?:\(\d+\))?', title))


def matches_chat_title(title, cal, key):
    labels = [label for label, value in cal.identities.items() if value == key]
    if len(labels) != 1:
        return False
    if key in cal.group_keys:
        return bool(re.fullmatch(re.escape(labels[0]) + r',?\([1-9]\d*\)', title))
    return title == labels[0] and not re.search(r'[,，]?\(?\d+\)$', title)


def _check(view, cal):
    if view.get('app') != cal.bundle_id:
        raise ValueError('当前前台不是已校准的微信')
    if view.get('truncated'):
        raise ValueError('控件树截断，不能确认观察范围完整')
    size = view.get('viewport', {})
    if tuple(size.get(k) for k in ('width', 'height')) != tuple(cal.viewport):
        raise ValueError('屏幕尺寸与校准不符')


def parse_list(view, cal):
    _check(view, cal)
    nodes = view.get('nodes', [])
    if not any(n.get('type') == 'NavigationBar' and is_list_title(text(n)) for n in nodes):
        raise ValueError('当前页面不是消息列表')
    if not 100 < cal.list_bottom <= cal.viewport[1]:
        raise ValueError('尚未校准消息列表底部')
    cells = [n for n in nodes if n.get('type') == 'Cell' and
             rect(n)[1] >= 100 and rect(n)[1] + rect(n)[3] <= cal.list_bottom and
             rect(n)[2] >= cal.viewport[0] * .8]
    counts = Counter(text(n).split(',')[0] for n in cells)
    result = []
    for cell in sorted(cells, key=lambda n: rect(n)[1]):
        label = text(cell)
        name, _, preview = label.partition(',')
        x, y, w, h = rect(cell)
        children = [n for n in nodes if y <= rect(n)[1] < y + h]
        muted = (True if '消息免打扰' in label or any(n.get('name') == 'bell_off_regular' for n in children)
                 else False if cal.muted_marker_verified and cal.list_region_complete else None)
        unread = bool(re.search(r'(?:^|,)(?:\d+条未读|未读)(?:,|$)', label))
        count = re.search(r'(?:^|,)(\d+)条未读(?:,|$)', label)
        # Unknown rows are visible for diagnostics but never verified contacts.
        signature = cal.identities.get(name, '')
        verified = bool(signature and counts[name] == 1 and name not in SERVICE_NAMES)
        result.append(ConversationCandidate(signature or 'unverified:' + name, name, y,
                    unread, muted, signature, verified, preview,
                    int(count.group(1)) if count else None, rect(cell)))
    return result


def parse_chat(view, cal, key, muted=None, identity_signature=None, *, list_verified=False):
    _check(view, cal)
    nodes = view.get('nodes', [])
    navs = [text(n) for n in nodes if n.get('type') == 'NavigationBar']
    if len(navs) != 1 or is_list_title(navs[0]):
        raise ValueError('聊天页标题无法识别')
    title = navs[0]
    expected = next((label for label, value in cal.identities.items() if value == key), '')
    identity = matches_chat_title(title, cal, key)
    if key in cal.profile_signatures:
        identity = identity and (list_verified or identity_signature == cal.profile_signatures[key])
    group = key in cal.group_keys
    messages, anchor = [], ''
    for n in sorted(nodes, key=lambda n: rect(n)[1]):
        label = text(n)
        if n.get('type') == 'Button' and re.fullmatch(r'\d{1,2}:\d{2}|(?:昨天|星期.).*', label):
            anchor = label
        if n.get('type') != 'Other':
            continue
        if group:
            # Message rows span the chat width; scrollbars and other controls do not.
            x, _, w, h = rect(n)
            if x != 0 or w < cal.viewport[0] * .8 or h <= 0 or ',' not in label:
                continue
        elif not re.match(r'^(?:我|' + re.escape(expected) + r'),', label):
            continue
        who, _, body = label.partition(',')
        if not who or not body:
            continue
        kind = 'text'
        for marker, kind_name in [('图片,', 'image'), ('语音,', 'voice'), ('视频,', 'video'), ('文件,', 'file'), ('链接,', 'link')]:
            # A comma in a group nickname makes its boundary ambiguous. A media
            # marker later in the label must not silently turn an image into text.
            if body.startswith(marker) or (group and who != '我' and ',' + marker in body):
                kind = kind_name
                break
        if group and who != '我':
            body = who + '：' + body
        messages.append(Message('outgoing' if who == '我' else 'incoming', body, kind, anchor))
    inputs = [n for n in nodes if n.get('type') in ('TextView', 'TextField')]
    if len(inputs) > 1:
        raise ValueError('多个输入框，无法确认草稿')
    draft = str(inputs[0].get('value', '')) if inputs else ''
    at_latest = any('垂直滚动条' in text(n) and n.get('value') == '100%' for n in nodes)
    send_failed = any(n.get('type') in ('Button','Image','StaticText') and
                      re.search(r'发送失败|消息未发送',text(n)) for n in nodes)
    return ChatSnapshot(key, messages, muted, draft, at_latest, identity, title, send_failed)
