import argparse
import json
import os
import signal
import sys
from dataclasses import asdict, fields
from pathlib import Path

from .config import Config, load_config
from .control import send_command
from .demo import DemoDriver, DemoSender, replay_demo, replay_multi_app_demo
from .adapters.registry import create_adapter
from .navigation import WechatDriver
from .phone import PhoneBridge
from .provider import CodexProvider, FakeProvider
from .service import Service
from .store import Store, private_dir
from .wechat import Calibration, SERVICE_NAMES


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
    if (not isinstance(cal.identities,dict) or not isinstance(cal.identity_probes,dict)
            or not isinstance(cal.profile_signatures,dict)
            or any(not isinstance(k,str) or not k or not isinstance(v,str) or not v
                   for k,v in cal.identities.items())
            or len(set(cal.identities.values())) != len(cal.identities)
            or not isinstance(cal.group_keys,list)
            or any(not isinstance(k,str) or k not in cal.identities.values() for k in cal.group_keys)
            or len(set(cal.group_keys)) != len(cal.group_keys)):
        raise ValueError('联系人校准格式不正确')
    return cal


def parser():
    p = argparse.ArgumentParser(description='聊天循环助手：Codex 生成文字，iPhone Use 操作手机。默认只预览。')
    p.add_argument('--config', default='config.json', help='JSON 配置路径')
    p.add_argument('--state-dir', help='覆盖业务状态目录，不改变 iPhone Use 目录')
    sub = p.add_subparsers(dest='command',required=True)
    run = sub.add_parser('run',help='启动常驻服务')
    run.add_argument('--send',action='store_true',help='本次启用自动发送，需完成联系人身份校准')
    run.add_argument('--demo',action='store_true',help='只运行模拟设备，不连接手机')
    run.add_argument('--once',action='store_true',help='执行一次检测后退出')
    demo = sub.add_parser('demo',help='离线回放，不连接设备或模型')
    demo.add_argument('--scenario',choices=('wechat','multi-app'),default='wechat')
    discover=sub.add_parser('discover',help='只读整理已有聊天候选')
    discover.add_argument('--full',action='store_true')
    scope=sub.add_parser('scope',help='非微信 App 每日聊天范围').add_subparsers(dest='operation',required=True)
    scope.add_parser('show')
    select=scope.add_parser('set')
    select.add_argument('--date',required=True)
    select.add_argument('--contacts',nargs='*',default=[])
    clear=scope.add_parser('clear')
    clear.add_argument('--date',required=True)
    campaigns=sub.add_parser('campaign',help='手动主动聊天轮次').add_subparsers(dest='operation',required=True)
    start=campaigns.add_parser('start')
    start.add_argument('--request-id',required=True)
    start.add_argument('--snapshot-id',required=True)
    start.add_argument('--contacts',nargs='+')
    start.add_argument('--instruction',default='')
    listing=campaigns.add_parser('list')
    listing.add_argument('--limit',type=int,default=100)
    listing.add_argument('--before',type=int)
    listing.add_argument('--active',action='store_true')
    cancel=campaigns.add_parser('cancel')
    cancel.add_argument('campaign_id',type=int)
    contact=sub.add_parser('contact').add_subparsers(dest='operation',required=True)
    unblock=contact.add_parser('unblock')
    unblock.add_argument('conversation')
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
            result = replay_multi_app_demo() if args.scenario=='multi-app' else replay_demo()
            emit(result)
            return 0 if result['ok'] else 1
        config = load_config(args.config)
        if args.state_dir:
            config.state_dir = str(Path(args.state_dir).expanduser().resolve())
        root = private_dir(config.state_dir)
        if args.command in ('status','pause','resume','stop','resolve','retry','rebaseline','cleanup',
                            'discover','scope','campaign','contact'):
            request = {'command':args.command,'expected_app_id':config.app_id}
            if args.command=='discover':
                request['full']=args.full
            elif args.command=='scope':
                request['command']={'show':'scope_get','set':'scope_set','clear':'scope_delete'}[args.operation]
                if args.operation!='show':
                    request['date']=args.date
                if args.operation=='set':
                    request['selected']=args.contacts
            elif args.command=='campaign':
                request['command']='campaign_'+args.operation
                if args.operation=='list':
                    request.update(limit=args.limit,active=args.active)
                    if args.before is not None:
                        request['before']=args.before
                if args.operation=='start':
                    request.update(request_id=args.request_id,snapshot_id=args.snapshot_id,
                                   instruction=args.instruction)
                    if args.contacts is not None:
                        request['selected']=args.contacts
            elif args.command=='contact':
                request['command']='contact_unblock'
            for key in ('job_id','outcome','conversation'):
                if hasattr(args,key):
                    request[key] = getattr(args,key)
            if hasattr(args,'campaign_id'):
                request['campaign_id']=args.campaign_id
            result = send_command(root,request)
            emit(result)
            return 0 if result.get('ok') else 1
        if args.command == 'calibrate':
            if config.app_id!='wechat':
                raise ValueError('calibration_is_wechat_only')
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
        if config.app_id!='wechat':
            raise ValueError('adapter_not_available')
        if args.demo:
            config.allowed,config.quiet_seconds = ['a','b','muted'],0
            driver,provider = DemoDriver(),FakeProvider()
        else:
            cal = load_calibration(config.calibration_path or root / 'calibration.json')
            if args.send:
                if config.provider != 'codex' or not config.allowed or not cal.verified:
                    raise ValueError('自动发送需要 Codex、指定联系人和已完成的身份校准')
                if not cal.muted_marker_verified or not cal.list_region_complete:
                    raise ValueError('尚未完成列表未读和免打扰标记校准')
                for key in config.allowed:
                    labels = [name for name,value in cal.identities.items() if value == key]
                    if len(labels) != 1 or labels[0] in SERVICE_NAMES:
                        raise ValueError('允许名单中存在尚未完成唯一列表名称映射的会话')
            bridge = PhoneBridge(config.plugin_path)
            if config.allowed:
                bridge.ready()
            driver = create_adapter(config,bridge)
            provider = CodexProvider(config) if config.provider == 'codex' else FakeProvider()
            if config.allowed and config.provider == 'codex':
                provider.check()
        store = Store(root / 'state.sqlite3',app_id=config.app_id)
        sender = DemoSender(store) if args.demo else None
        mode = 'send' if args.send or args.demo else 'preview'
        service = Service(config,store,driver,provider,sender=sender,mode=mode)
        for sig in (signal.SIGINT,signal.SIGTERM):
            signal.signal(sig,lambda signum,frame: service.request({'command':'stop'}))
        emit({'ok':True,'state':'starting','mode':mode,'provider':'离线模拟' if args.demo else config.provider,
              'allowed_count':len(config.allowed),'state_dir':str(root),
              'note':'先保存未读再进入会话；只处理允许且列表未显示免打扰的文字单聊或群聊。'})
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
