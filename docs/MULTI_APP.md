# 多 App 引擎与手动主动聊天

本次扩展复用原有循环、Codex 生成和发送核对。真实 UI 仅保留微信；青藤之恋、积目和 Soul 共用通用规则，真实页面后续接入。离线演示中的 `jimu` 只表示规则命名空间，不代表已经支持积目。

## 配置与范围

`app_id` 默认 `wechat`，支持配置值 `wechat`、`qingteng`、`jimu`、`soul`，后三个当前不能启动真实服务。一次服务仅处理一个 App；手机服务租约按共享 iPhone Use Runtime 互斥，切换 App 前先停止当前服务。同一数据库中的会话、任务、策略、预览和恢复记录按 App 隔离；控制客户端也发送目标 App 标识，配置与运行服务不一致时拒绝操作。

Soul 的配置使用 `"app_id": "soul"`，适用下文全部非微信范围与轮次规则；仅处理已有聊天列表，不进入推荐或匹配页面。真实页面接入步骤见 [Soul 适配计划](superpowers/plans/2026-10-24-soul-adapter.md)。配置可识别不代表真实界面已经适配。

- 微信：继续配置 `allowed`，完成身份校准和首次预览后显式 `run --send`。每日范围命令不能改变微信名单。
- 其他 App：不设置静态 `allowed`，没有逐联系人手工校准前置。当日无每日清单时范围为全部已有聊天；明确保存空清单时范围为空；删除当天记录后恢复默认全部。
- 每日规则按照服务启动时固定的本机时区计算自然日；仅约束范围，不启动主动聊天。状态返回实际时区和当前日期。
- 免打扰及状态未知的会话不发送；没有预先人工校准不会导致非微信候选被默认排除，但发送时仍要确认没有跳到另一个会话。初始未读只有红点、无法确定消息分界时，该会话需要人工处理，不把历史全文当作新消息。

## 先运行离线演示

```sh
PYTHONPATH=src python3 -m wechat_autoreply demo
PYTHONPATH=src python3 -m wechat_autoreply demo --scenario multi-app
```

两个命令都不连接手机、Codex 或账号。第二个使用真实通用服务和 Sender、合成页面、虚拟时钟，验证每日子集、每人最多三次、两小时间隔和回复后取消。`ok: true` 仅表示回放通过。

## 发现候选

以下命令作用于已运行的服务，在另一个终端使用同一配置执行。当前只有微信可以启动真实服务；非微信示例用于说明待接入适配器后的操作方式。

```sh
PYTHONPATH=src python3 -m wechat_autoreply --config config.json discover --full
```

返回 `snapshot_id`、候选本地 ID、可见名称/预览、免打扰和覆盖信息；不会发送。候选只来自已有聊天列表，不进入推荐、匹配或主页采集。没有显示的资料不猜测。

快照在 300 秒后过期。`complete: false` 时，省略目标意味着全量请求，将明确拒绝；可以用 `--contacts` 选择已观察子集。微信现有分页上限仍受 `max_scan_pages` 约束，`--full` 不会把有界扫描伪装成完整列表。

范围策略变化后，旧快照不能再用于省略目标的全量启动，需要重新发现；显式选择的子集会按最新范围复核。首次主动读取没有可靠的新输入分界或可靠的空边界时，会暂停该任务，不能把“没有边界证据”当作“没有新消息”。已有未处理或失败的回复任务也会阻止主动开场。

## 非微信每日名单

日期按 status 中的时区填写。示例 ID 必须替换为该 App 发现的本地 ID。

```sh
PYTHONPATH=src python3 -m wechat_autoreply --config config.json scope show
PYTHONPATH=src python3 -m wechat_autoreply --config config.json scope set --date 2026-10-24 --contacts contact_a contact_b
```

明确设置当天不发送与删除清单是不同动作：

```sh
# 保存空名单：当天不发送
PYTHONPATH=src python3 -m wechat_autoreply --config config.json scope set --date 2026-10-24
# 删除当天记录：恢复默认全部已有会话
PYTHONPATH=src python3 -m wechat_autoreply --config config.json scope clear --date 2026-10-24
```

新日期不沿用昨天名单。策略变更在服务主线程处理后生效；socket 返回 `accepted: true, state: queued` 只表示已入控制队列，需查看 status 确认处理完成。已经提交手机的消息不能撤回。移出范围会结束相关主动轮次，之后恢复范围不会自动复活旧轮次。

## 手动开始一轮

```sh
PYTHONPATH=src python3 -m wechat_autoreply --config config.json campaign start --request-id ROUND_REQUEST_ID --snapshot-id SNAPSHOT_ID --contacts contact_a contact_b
PYTHONPATH=src python3 -m wechat_autoreply --config config.json campaign list
PYTHONPATH=src python3 -m wechat_autoreply --config config.json campaign cancel CAMPAIGN_ID
```

`ROUND_REQUEST_ID` 为本次指令的唯一 ID，`SNAPSHOT_ID` 来自候选发现。省略 `--contacts` 使用完整快照中当天范围允许的会话；可选 `--instruction` 设置本轮目标或话题，口吻仍沿用风格文档。目标在启动时固定，后来出现的人不会加入。

同一 request ID 重复提交相同指令返回原结果；复用 ID 改指令将报冲突。另一个请求遇到尚未结束的同人轮次会返回 existing，不重置次数。结束后需要用新请求 ID 明确开启下一轮。

status 默认只显示最近 100 个轮次摘要和各状态数量，不回传完整任务说明。更多历史通过 `campaign list --limit 100 --before CAMPAIGN_ID` 翻页，或使用 `--active` 只查看有效轮次。单页上限 100。

## 次数、取消与恢复

每个目标独立执行：首次到期 → 未回复至少 2 小时后第二次 → 再至少 2 小时后第三次 → 结束。时间是最早可执行时间，不是准点发送保证；未读回复优先，AI 与页面操作会增加延迟。到期以确认发送时刻计算；生成失败不消耗已发送次数，已提交但结果不确定占用当前序号并停止该会话后续发送。

收到回复时，取消本轮剩余跟进并进入正常回复；观察到用户手动发出的新消息也结束本轮。其他联系人回复不终止本人的轮次。明确拒绝继续交流会保存停止联系状态；不因跨日或再次启动清除。解除命令仅解除停止状态，不发送或自动开启轮次：

```sh
PYTHONPATH=src python3 -m wechat_autoreply --config config.json contact unblock CONTACT_ID
```

预览不提交、不推进实际次数。重启默认回到预览，显式启用发送后，已有跟进任务仍至少等待新的 7200 秒保守窗口。运行中检测到调钟异常时停止主动发送，需显式恢复；不会补齐离线错过的多个间隔。未知发送继续使用原有只读核对和人工 resolve 流程，不补发。

主动任务的 `resolve --outcome sent` 在有提交检查点时推进本轮序号，恢复后先只读核对并更新基线；不会补发已确认消息。`retry` 只复用尚未提交的原轮次槽位。`handled`、`abandoned` 或显式 `rebaseline` 会结束该会话旧轮次；其中 `rebaseline` 仍需核对当前页面。普通回复的人工处理命令保持原语义。

服务会私下记录自己尚未提交的输入。任务在输入后失效时，只有精确读回仍为同一文本才会清理；暂停期间不强行操作，恢复或重启后的第一步处理。若用户已修改内容，保留原样并提示人工检查，不把人工草稿擦掉。已提交但结果不确定的文字不按未提交草稿自动清理。

本地状态按版本事务式升级，旧数据会先创建权限 600 的 `.pre-v1.bak` / `.pre-v2.bak` 恢复副本。备份可能包含聊天数据，留在私有状态目录，不能上传；用户可在确认迁移后自行归档或删除。`cleanup` 按 retain_days（默认 7 天）删除过期发现和已结束轮次历史，保护有效轮次、未解决发送和待核对记录。过期幂等结果压缩为不重复执行的记录，旧请求 ID 返回 expired；需要真正新开一轮时使用新 ID。
