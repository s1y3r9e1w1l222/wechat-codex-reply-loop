import argparse
import json
import os
import signal
import sys
from dataclasses import asdict, fields
from pathlib import Path

from .config import Config, load_config
from .control import send_command
from .demo import DemoDriver, DemoSender, replay_demo
from .navigation import WechatDriver
from .phone import PhoneBridge
from .provider import CodexProvider, FakeProvider
from .service import Service
from .store import Store, private_dir
from .wechat import Calibration


def load_calibration(path):
    if not path or not Path(path).is_file():
        return Calibration()
    p = Path(path)
    if p.stat().st_size > 65536:
        raise ValueError('校准文件过大')
    data = json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(data,dict) or set(data) - {f.name for f in fields(Calibration)}:
        raise ValueError('校准文件格式不正确')
    cal = Calibration(**data)
    if not isinstance(cal.identities,dict) or not isinstance(cal.identity_probes,dict):
        raise ValueError('联系人校准格式不正确')
    return cal


def parser():
    p = argparse.ArgumentParser(description='微信 AI 助手：Codex 生成回复，iPhone Use 操作手机。默认只预览。')
    p.add_argument('--config', default='config.json', help='JSON 配置路径')
    p.add_argument('--state-dir', help='覆盖业务状态目录，不改变 iPhone Use 目录')
    sub = p.add_subparsers(dest='command',required=True)
    run = sub.add_parser('run',help='启动常驻服务')
    run.add_argument('--send',action='store_true',help='本次启用自动发送，需完成联系人身份校准')
    run.add_argument('--demo',action='store_true',help='只运行模拟设备，不连接手机')
    run.add_argument('--once',action='store_true',help='执行一次检测后退出')
    sub.add_parser('demo',help='回放排序、免打扰和 AI 乱序完成')
    sub.add_parser('calibrate',help='只读校准当前微信列表，不发送消息')
    for name in ('status','pause','resume','stop','cleanup'):
        sub.add_parser(name)
    resolve = sub.add_parser('resolve')
    resolve.add_argument('job_id',type=int)
    resolve.add_argument('--outcome',choices=['sent','handled','abandoned'],required=True)
    retry = sub.add_parser('retry')
    retry.add_argument('job_id',type=int)
    baseline = sub.add_parser('rebaseline')
    baseline.add_argument('conversation')
    return p


def emit(value):
    print(json.dumps(value,ensure_ascii=False,indent=2),flush=True)


def main(argv=None):
    args = parser().parse_args(argv)
    bridge, store = None, None
    try:
        if args.command == 'demo':
            result = replay_demo()
            emit(result)
            return 0 if result['ok'] else 1
        config = load_config(args.config)
        if args.state_dir:
            config.state_dir = str(Path(args.state_dir).expanduser().resolve())
        root = private_dir(config.state_dir)
        if args.command in ('status','pause','resume','stop','resolve','retry','rebaseline','cleanup'):
            request = {'command':args.command}
            for key in ('job_id','outcome','conversation'):
                if hasattr(args,key):
                    request[key] = getattr(args,key)
            result = send_command(root,request)
            emit(result)
            return 0 if result.get('ok') else 1
        if args.command == 'calibrate':
            target = Path(config.calibration_path or root / 'calibration.json')
            if target.exists():
                raise ValueError('校准文件已存在；请先备份并移走它，避免覆盖联系人校准')
            bridge = PhoneBridge(config.plugin_path)
            bridge.ready()
            cal = bridge.calibrate()
            if cal.list_bottom == 0:
                raise ValueError('当前屏幕尺寸尚未校准，请在 Codex 中完成界面校准')
            private_dir(target.parent)
            target.write_text(json.dumps(asdict(cal),ensure_ascii=False,indent=2))
            os.chmod(target,0o600)
            emit({'ok':True,'calibration':str(target),'status':'列表标记已读取；普通联系人身份尚待校准',
                  'muted_marker_verified':cal.muted_marker_verified,'auto_send_ready':False})
            return 0
        if args.demo:
            config.allowed,config.quiet_seconds = ['a','b','muted'],0
            driver,provider = DemoDriver(),FakeProvider()
        else:
            cal = load_calibration(config.calibration_path or root / 'calibration.json')
            if args.send:
                if config.provider != 'codex' or not config.allowed or not cal.verified:
                    raise ValueError('自动发送需要 Codex、指定联系人和已完成的身份校准')
                for key in config.allowed:
                    probe = cal.identity_probes.get(key,{})
                    if (key not in cal.profile_signatures or not probe.get('steps') or
                            not probe.get('back_steps') or not cal.muted_marker_verified):
                        raise ValueError('允许名单中存在尚未完成身份或免打扰校准的联系人')
            bridge = PhoneBridge(config.plugin_path)
            if config.allowed:
                bridge.ready()
            driver = WechatDriver(bridge,cal,config.max_scan_pages)
            provider = CodexProvider(config) if config.provider == 'codex' else FakeProvider()
            if config.allowed and config.provider == 'codex':
                provider.check()
        store = Store(root / 'state.sqlite3')
        sender = DemoSender(store) if args.demo else None
        mode = 'send' if args.send or args.demo else 'preview'
        service = Service(config,store,driver,provider,sender=sender,mode=mode)
        for sig in (signal.SIGINT,signal.SIGTERM):
            signal.signal(sig,lambda signum,frame: service.request({'command':'stop'}))
        emit({'ok':True,'state':'starting','mode':mode,'provider':'离线模拟' if args.demo else config.provider,
              'allowed_count':len(config.allowed),'state_dir':str(root),
              'note':'预览会读取并可能标记消息已读；自动发送只处理允许且非免打扰的文字单聊。'})
        service.run(once=args.once)
        return 0
    except Exception as e:
        emit({'ok':False,'error':getattr(e,'code',str(e) if isinstance(e,(ValueError,RuntimeError,FileNotFoundError)) else type(e).__name__)})
        return 1
    finally:
        if bridge:
            bridge.close()
        if store:
            store.close()


if __name__ == '__main__':
    sys.exit(main())
