from dataclasses import dataclass
from typing import Tuple, Protocol

from ..models import ConversationCandidate


@dataclass(frozen=True)
class AdapterCapabilities:
    app_id: str
    verified: bool = False
    revision: str = ''
    supports_full_discovery: bool = False


@dataclass(frozen=True)
class ScanResult:
    candidates: Tuple[ConversationCandidate, ...]
    complete: bool
    coverage: dict
    snapshot_id: str
    adapter_token: str


class AppAdapter(Protocol):
    app_id: str
    capabilities: AdapterCapabilities

    def scan(self, cancel=None, *, full=False) -> ScanResult: ...
    def read_chat(self, key, cancel=None): ...
    def return_to_list(self, cancel=None): ...
    def transaction(self, callback, cancel=None): ...
    def preview_requirements(self, config): ...
    def preview_evidence(self, scan): ...
