from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class Message:
    direction: str
    text: str
    kind: str = 'text'
    anchor: str = ''

    def token(self):
        return self.direction, self.kind, self.text, self.anchor


@dataclass(frozen=True)
class ConversationCandidate:
    key: str
    label: str
    position: float
    unread: bool
    muted: Optional[bool]
    signature: str = ''
    identity_verified: bool = False
    preview: str = ''
    unread_count: Optional[int] = None
    rect: Tuple[float, ...] = ()


@dataclass
class ChatSnapshot:
    key: str
    messages: List[Message]
    muted: Optional[bool]
    draft: str = ''
    at_latest: bool = False
    identity_verified: bool = False
    title: str = ''
    send_failed: bool = False
    initial_unread: Optional[Tuple[Message, ...]] = None


@dataclass(frozen=True)
class Alignment:
    appended: Tuple[Message, ...] = ()
    ambiguous: bool = False
    reason: str = ''


@dataclass(frozen=True)
class SendResult:
    status: str
    reason: str = ''
    snapshot: Optional[ChatSnapshot] = None


def messages_json(messages):
    return [asdict(m) for m in messages]


def messages_from(items):
    return [Message(**item) for item in items]
