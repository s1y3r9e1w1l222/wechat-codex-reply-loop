# Contributing

欢迎通过 Issue 和 Pull Request 改进。当前目标是先稳定中文微信的有界文字单聊流程；支持更多尺寸、语言和 App 前，请先描述页面差异、身份依据和失败处理。

## 开发与验证

Python 3.9+，源代码在 `src/wechat_autoreply`。导入模块和自动测试不得连接手机、调用在线 AI 或发送真实消息。

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src scripts
PYTHONPATH=src python3 -m wechat_autoreply demo
python3 scripts/check_release.py --history
```

修复错误时先增加能复现问题的合成测试，再修改实现。涉及排序、生命周期、发送检查点或身份校验的改动，需覆盖失败和取消路径。说明改了什么、为什么、运行了哪些检查，以及哪些真实设备行为还没验证。

## 必须保持的行为

- 默认空允许名单、预览；不根据显示名称或坐标独自判断联系人身份。
- 免打扰或状态不明不回复；仅文字单聊。收到新输入时旧草稿失效，新版本排到队尾。
- AI 回调不能直接操作数据库或手机；生成不能越过共享手机锁去发送。
- 发送前持久化检查点；结果不确定禁止重放。不能用“请求成功”代替新消息气泡证据。
- 不修改用户已安装插件缓存，不读取或复制登录令牌。

## 数据与真机测试

只提交合成、脱敏夹具。不要提交真实姓名、微信号、聊天原文、原始控件树、截图、设备 UDID、开发者签名或本机配置。默认 Git 忽略只能保护常见路径，不能代替检查 diff。

真机测试需得到设备所有者对测试对象和发送内容的明确授权。自动 CI 不做真机测试。项目维护者的个人设备和联系人不是公共测试资源。

安全问题见 [SECURITY.md](SECURITY.md)。首次公开发布前按 [RELEASING.md](docs/RELEASING.md) 检查；普通贡献无需创建发布包。
