import json
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class Config:
    app_id: str = 'wechat'
    mode: str = 'preview'
    allowed: list = field(default_factory=list)
    plugin_path: str = ''
    state_dir: str = '~/.local/share/wechat-autoreply'
    calibration_path: str = ''
    model: str = ''
    codex_command: str = 'codex'
    provider: str = 'codex'
    persona_path: str = ''
    poll_seconds: float = 2
    quiet_seconds: float = 2
    max_wait_seconds: float = 8
    ai_workers: int = 2
    request_timeout: float = 90
    max_messages: int = 20
    max_context_chars: int = 8000
    max_reply_chars: int = 300
    retain_days: int = 7
    max_scan_pages: int = 3


def load_config(path):
    path = Path(path).expanduser().resolve()
    if path.stat().st_size > 65536:
        raise ValueError('配置文件过大')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('配置必须是 JSON 对象')
    unknown = set(data) - {f.name for f in fields(Config)}
    if unknown:
        raise ValueError('配置存在未知字段')
    c = Config(**data)
    if c.app_id not in ('wechat', 'qingteng', 'jimu', 'soul'):
        raise ValueError('unknown_app')
    if c.mode != 'preview':
        raise ValueError('配置中的默认模式必须为 preview；自动发送需本次启动显式启用')
    if (not isinstance(c.allowed, list) or len(c.allowed) > 100 or
            any(not isinstance(x, str) or not x or len(x) > 200 for x in c.allowed)
            or len(set(c.allowed)) != len(c.allowed)):
        raise ValueError('联系人名单格式不正确或重复')
    if c.app_id != 'wechat' and c.allowed:
        raise ValueError('non_wechat_uses_daily_scope')
    numeric = dict(poll_seconds=(0.5, 60), quiet_seconds=(0, 10),
                   max_wait_seconds=(1, 60), ai_workers=(1, 2),
                   request_timeout=(1, 120), max_messages=(1, 20),
                   max_context_chars=(100, 8000), max_reply_chars=(1, 300),
                   retain_days=(1, 365), max_scan_pages=(1, 10))
    integer_fields = {'ai_workers', 'max_messages', 'max_context_chars',
                      'max_reply_chars', 'retain_days', 'max_scan_pages'}
    for key, (low, high) in numeric.items():
        value = getattr(c, key)
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not low <= value <= high or
                (key in integer_fields and not isinstance(value, int))):
            raise ValueError(key + ' 超出范围或类型不正确')
    if c.max_wait_seconds < c.quiet_seconds:
        raise ValueError('最长合并等待不能小于安静期')
    for key in ('plugin_path', 'state_dir', 'calibration_path', 'codex_command',
                'model', 'provider', 'persona_path'):
        value = getattr(c, key)
        if not isinstance(value, str) or '\x00' in value or len(value) > 4096:
            raise ValueError(key + ' 格式不正确')
    if c.provider not in ('codex', 'fake'):
        raise ValueError('未知 AI provider')
    if not c.codex_command or c.codex_command.startswith('-'):
        raise ValueError('Codex 可执行文件路径不正确')
    for key in ('plugin_path', 'state_dir', 'calibration_path', 'persona_path'):
        value = getattr(c, key)
        if value:
            p = Path(value).expanduser()
            setattr(c, key, str((p if p.is_absolute() else path.parent / p).resolve()))
    return c
