def create_adapter(config, bridge):
    if getattr(config, 'app_id', 'wechat') != 'wechat':
        raise ValueError('adapter_not_available')
    from ..navigation import WechatDriver
    from ..cli import load_calibration
    from pathlib import Path
    from .wechat import WechatAdapter
    cal = load_calibration(config.calibration_path or Path(config.state_dir) / 'calibration.json')
    return WechatAdapter(WechatDriver(bridge, cal, config.max_scan_pages))
