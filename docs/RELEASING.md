# GitHub 发布准备

项目公开仓库为 [s1y3r9e1w1l222/wechat-codex-reply-loop](https://github.com/s1y3r9e1w1l222/wechat-codex-reply-loop)。许可证为 MIT，版权署名为项目 contributors。以下脚本仅执行本地检查与打包，不会自动上传代码或公开数据。

## 本地检查

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src scripts
PYTHONPATH=src python3 -m wechat_autoreply demo
python3 scripts/check_release.py --history
git diff --check
```

检查跟踪文件和全部可达历史。自动检查报告类别和位置，不展示匹配原文；仍需人工确认无真实聊天、姓名、设备信息、私有配置、日志、截图、密钥或签名材料。不要压缩整个工作目录，也不要打包上游安装缓存。不要强制加入 `.state`、`.superpowers` 等忽略目录。

确认所有公开文件已提交，工作区除忽略数据外保持干净，然后生成源代码包：

```sh
python3 scripts/prepare_release.py dist/wechat-codex-reply-loop-source.zip
```

脚本拒绝覆盖现有文件；检查历史并仅运行 `git archive HEAD`，输出对应提交和 SHA-256。再解压到临时空目录，运行离线 demo，核对压缩包没有本机数据。这个包不包含 Git 历史，但直接公开 Git 仓库会包含历史，因此两者都要检查。

## 公开前由仓库所有者完成

1. 确认目标仓库、发布版本与 MIT 授权，将已审查且通过脱敏检查的源码发布至上述仓库。
2. 启用私密漏洞报告入口，检查 Actions 权限只读；启用适用的秘密扫描和推送保护。
3. 首次公开标注 experimental / alpha，说明仍需本机联系人校准，并链接验证范围。不要使用未脱敏截图。
4. GitHub CI 真正运行通过后再建立版本标签和 Release；附源码包和生成的校验值。首次公开、标签和发布需单独执行，当前脚本不会代办。

## CI 的外部操作

工作流只在 push / pull_request / workflow_dispatch 上运行离线测试；不接受手机或 Codex 凭据，不上传运行数据，不自动发布。使用 [checkout v4.3.0](https://github.com/actions/checkout/releases/tag/v4.3.0) 和 [setup-python v5.6.0](https://github.com/actions/setup-python/releases/tag/v5.6.0) 的固定提交。依赖升级需明确检查差异和 CI，不让标签漂移静默改变执行内容。
