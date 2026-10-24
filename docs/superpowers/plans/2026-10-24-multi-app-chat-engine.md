# Multi-App Chat Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在保留微信原规则的基础上，交付支持非微信每日范围与手动主动轮次的统一聊天引擎。

**Architecture:** 使用活动 App 对应的适配器观察和操作手机；回复、主动首发与主动跟进共用数据库、可执行队列和发送检查点。每个服务只处理一个 App，所有持久化查询按 App 隔离；两个真实新 App 的适配分别执行附属计划。

**Tech Stack:** Python 3.9+ 标准库、SQLite、unittest、Codex CLI、iPhone Use 0.3.8；不新增运行依赖。

**Spec:** `docs/superpowers/specs/2026-10-24-multi-app-chat-engine-design.md`（用户已确认，2026-10-24）。

## Global Constraints

- 主动轮次必须手动触发；没有按未聊天时长自动开始的定时器。
- 每个会话每轮最多 3 次，包含首次；未回复时跟进间隔 7200 秒，从上次确认发送时刻计算。
- 微信保留静态允许名单、身份校准和逐联系人首次预览规则。
- 非微信当天未设置名单为全部已有会话；显式空名单为不发送；没有逐联系人人工校准前置。
- 只处理已有聊天列表；免打扰会话跳过；不能确认当前接收对象时不提交发送。
- 对方回复、人工自行发送、移出范围或停止联系均结束该会话当前轮次；跨日不自动重开。
- 同一手机仅一个活动 App 服务；继续使用现有 Runtime 目录下的 InstanceLease 和事务锁。
- 默认预览，发送必须显式启用；复用 persona_path；保留 20 条/8000 字符上下文、300 字回复、2 个 AI worker、90 秒默认生成时限。
- 新 App 未经过真机勘察与验收时必须标为未验证并禁用真实发送；当前不操作手机、不向新 App 联系人发送。
- 保留公开仓库名、Python 模块、原 CLI 与旧配置兼容；本地私有资料不进入提交。
- 用户已选本会话由主代理连续实施、最后独立评审；模型路由遵循用户 AGENTS.md。开始实施前先完成本计划确认与 worktree 流程。

## Review Focus

- 相同会话 ID、任务 ID 或策略日期来自另一个 App：任何读取、更新、恢复和清理都不能跨 App（Task 2）。
- 同一手动请求 ID 重复使用但目标或指令变化：返回冲突，不创建第二轮或重置次数（Task 4）。
- 发现结果部分覆盖、过期或在策略变更后使用：不能被当成“全部候选”启动（Tasks 3、7）。
- 时钟跳变、重启和清理历史后：不缩短两小时、不连发补齐、不删除活跃轮次依赖（Tasks 2、4、6）。
- 新 App 只有未读红点，没有可靠未读条数或消息分界：不能捏造对方新输入，也不能把整段历史当作待回复内容（Tasks 1、5、7）。

---

## 交付划分与文件职责

本计划产出独立可运行的通用引擎、微信兼容层与离线多 App 演示。青藤之恋、积目的真实控件适配分别见同目录 `2026-10-24-qingteng-adapter.md`、`2026-10-24-jimu-adapter.md`；依赖本计划契约，不把未勘察的 UI 坐标写成实现承诺。

- 新建 `adapters/base.py`、`adapters/wechat.py`、`adapters/registry.py`：适配契约、微信包装与注册。
- 新建 `migrations.py`：私有备份与事务式 schema 升级；`store.py` 保持唯一业务写入口。
- 新建 `scope.py`：每日范围与候选发现；新建 `campaigns.py`：主动轮次、幂等、截止时间和时钟守卫。
- 新建 `generation.py`：三类文字请求与生成结果；`provider.py` 继续封装 Codex 子进程。
- 修改 `scheduler.py`、`sending.py`、`service.py`：共享队列、发送复核和取消，不复制第二个发送循环。
- 修改 `cli.py`、`config.py`、`control.py`、`demo.py`：选择 App、命令入口、状态与离线回放。
- 新建 `tests/chat_support.py`：可控时钟、合成适配器和固定消息；每个任务增加独立测试文件或扩展现有文件。

所有下文相对源码路径均以 `src/wechat_autoreply/` 为根；测试路径以仓库根为准。

### Task 1: 固定适配契约并包装现有微信

**Files:** Create `adapters/__init__.py`, `adapters/base.py`, `adapters/wechat.py`, `adapters/registry.py`, `tests/chat_support.py`, `tests/test_adapters.py`; Modify `models.py`。

**Interfaces:**
- `AdapterCapabilities(app_id: str, verified: bool, revision: str, supports_full_discovery: bool)`；详细页面能力证据保存在适配器本机验证记录中。
- `ScanResult(candidates: Tuple[ConversationCandidate, ...], complete: bool, coverage: dict, snapshot_id: str, adapter_token: str)`。
- `AppAdapter.app_id`, `.capabilities`, `.bridge`；`.scan(cancel=None, *, full=False) -> ScanResult`，`.read_chat(key: str, cancel=None) -> ChatSnapshot`，`.return_to_list(cancel=None) -> None`，`.transaction(callback, cancel=None)`；事务 session 保留 `read_chat/input_text/clear_own_draft/submit/return_to_list` 签名。
- `.preview_requirements(config) -> Set[str]`、`.preview_evidence(scan: ScanResult) -> Set[str]`：微信返回现有联系人指纹，非微信返回经过验证的适配器/页面能力 token。
- `ChatSnapshot.initial_unread: Optional[Tuple[Message, ...]] = None`：仅适配器有真实分界证据时提供，且必须是当前上下文末尾连续的对方消息。`identity_verified` 保留兼容名称，表示当前会话绑定证据，不等于非微信人工校准。
- `WechatAdapter(driver: WechatDriver)` 包装现有导航与校准；`create_adapter(config, bridge) -> AppAdapter` 初期仅接受 `wechat`，其他返回 `adapter_not_available`。
- 测试 `FakeClock(wall=0.0)` 的 `.wall()`、`.monotonic()`、`.advance(seconds)`、`.jump_wall(seconds)`；`SyntheticAdapter(app_id, scans=(), chats=None)` 支持确定性入队观察、事务动作日志和注入错误。

- [x] **Step 1: 新增失败契约测试。** `test_wechat_wrapper_preserves_calibration_and_rows` 断言包装后 candidate 与原驱动相同；`test_unverified_adapter_cannot_supply_preview_ticket` 断言未验证能力不产生发送资格；`test_unread_dot_does_not_invent_boundary` 断言 `initial_unread is None`。
- [x] **Step 2: 运行 RED。** `PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_adapters.py' -v`，应因缺少新接口失败。
- [x] **Step 3: 实现上述接口与测试支持。** 引入 Protocol/dataclass 但不改变现有 driver 的坐标或解析规则。微信完整发现只有证实到达末尾才能置 `complete=True`；原有上限扫描不能冒充完整发现。
- [x] **Step 4: 运行 GREEN。** 运行 Task 1 命令及 `test_navigation.py`、`test_wechat.py` 对应 discover，均通过。
- [x] **Step 5: 提交。** `git commit -m "refactor: define chat adapter contract and wrap WeChat"`，仅暂存本任务文件。

### Task 2: App 隔离、迁移和可执行队列存储

**Files:** Create `migrations.py`, `tests/test_migrations.py`; Modify `store.py`, `tests/test_store.py`。

**Interfaces:**
- `Store(path, app_id='wechat', clock=time.time)`：现有方法签名和返回的本地 conversation key 保持兼容；所有 SQL 自动限定当前 `app_id`，全局 job ID 也不能用于越权访问。
- `migrate(connection, path: Path) -> None`：`PRAGMA user_version` 从旧 0 升至 1；迁移前用 SQLite backup 创建权限 600 的私有恢复副本。旧数据库记录均归属 `wechat`，恢复与预览语义不变；未知较新版本拒绝打开。
- conversations 主键改为 `(app_id, key)`；reply_jobs、message_observations、send_attempts、service_state 增加 App 关联；外键/联表同时使用 App 和 ID。
- reply_jobs 新增 `kind='reply'`, `campaign_id=None`, `sequence=None`, `not_before=0`, `policy_version=0`；`create_job(..., *, kind='reply', campaign_id=None, sequence=None, not_before=0, policy_version=0)`。
- `ready_jobs(now: float) -> List[dict]`、`head_job(now: Optional[float]=None)`：过滤未来任务，按 reply 优先、其后主动任务、各类 ID 升序；保留 `active_jobs()` 用于状态，不再拿它直接决定发送顺序。
- 新建 scope_policies、discovery_snapshots、campaigns、manual_requests、contact_controls 表；campaigns 每行对应当前 App 一个目标的一轮，包含 `status/next_sequence/confirmed_count/last_confirmed_at/not_before/reason`，每个有效会话轮次与每轮序号均有唯一约束。具体列由 Tasks 3/4 的接口决定并在本任务完成建表。

- [x] **Step 1: 写失败测试。** 从合成旧 schema 迁移并断言旧 uncertain、预览 token、消息记录保持；测试跨 App 同名隔离及 job ID 越权。断言：
  ```python
  self.assertIsNone(other_app.job(wechat_job_id))
  self.assertEqual(store.head_job(now=7199)['kind'], 'reply')
  self.assertTrue(backup_path.stat().st_mode & 0o077 == 0)
  ```
  另测迁移中断回滚、重复打开不重复迁移、未来 schema 拒绝、清理不删除活跃轮次/未解决发送依赖。
- [x] **Step 2: RED。** 分别 discover `test_migrations.py` 和 `test_store.py`，新用例失败且旧基线仍有明确结果。
- [x] **Step 3: 实现迁移与存储接口。** 清理按 App 过滤且按引用保护轮次、发送证据、幂等记录；错误不输出数据库内容。迁移备份不进入仓库。
- [x] **Step 4: GREEN。** 运行 Task 2 测试和现有 `test_scheduler.py`、`test_sending.py`；仅对构造注入调整夹具，不删除旧行为断言。
- [x] **Step 5: 提交。** `git commit -m "feat: isolate app state and persist scheduled chat jobs"`。

### Task 3: 每日范围与候选完整性

**Files:** Create `scope.py`, `tests/test_scope.py`; Modify `config.py`, `tests/test_config.py`。

**Interfaces:**
- `Config.app_id: str = 'wechat'`；注册允许值 `wechat/qingteng/jimu`，未实现适配器仍禁止运行；非微信配置中非空旧 `allowed` 返回冲突错误，使用每日策略接口设置名单。
- `ScopePolicy(store, config, timezone, clock)`；`today() -> str` 返回启动时固定本机时区的 ISO 日期，`set_daily(date: str, selected: Sequence[str]) -> dict`，`delete_daily(date: str) -> None`，`allows(key: str, *, date=None) -> bool`，`version() -> int`，`describe() -> dict`。
- 微信 `.allows` 只读取 config.allowed；微信调用 set/delete daily 返回 `wechat_uses_static_allowlist`。非微信无当天记录为全部，空 selected 为一个人也不允许；每次变更递增版本。
- `DiscoveryCatalog(store)`；`save(scan: ScanResult, now: float) -> str`，`resolve(snapshot_id: str, selected: Optional[Sequence[str]], policy: ScopePolicy, now: float) -> Tuple[str,...]`。全量请求 selected=None 必须完整；指定子集可从部分结果解析。快照必须属于当前 App、年龄不超过 300 秒，启动时重查策略、免打扰及停止状态；过期需重新发现，不静默扩大集合。
- 命名与时区解析使用标准库；本机时区固定 token 保存到状态。跨日增加有效策略版本，使旧草稿重查。

- [x] **Step 1: 写失败测试。** 覆盖微信空名单仍拒绝、非微信未设置全部/显式空集合/删除恢复全部、日期过期、两个 App 同日不同名单；完整/部分/300 秒过期快照。`test_partial_snapshot_cannot_start_all` 断言错误码 `discovery_incomplete`，指定已观察子集可解析。
- [x] **Step 2: RED。** discover `test_scope.py` 与 `test_config.py`。
- [x] **Step 3: 实现 ScopePolicy 与 DiscoveryCatalog。** 不读取主页，不补造未知时间；候选键仅在当前 App 有效。保留未知配置键拒绝策略。
- [x] **Step 4: GREEN。** 运行本任务与 `test_adapters.py`、`test_migrations.py`。
- [x] **Step 5: 提交。** `git commit -m "feat: add daily chat scope and explicit discovery coverage"`。

### Task 4: 手动主动轮次、幂等与计时

**Files:** Create `campaigns.py`, `tests/test_campaigns.py`。

**Interfaces:**
- `CampaignManager(store, policy, clock_guard)`；`start(request_id: str, targets: Sequence[str], instruction: str='') -> dict` 返回 `created/existing/skipped` 的 key→campaign ID/原因；每个目标单独一轮。
- `cancel(campaign_id: int, reason: str) -> None`；`end_for_conversation(key: str, reason: str) -> None`；`observe_incoming(key: str) -> None`；`observe_manual_outgoing(key: str) -> None`；`stop_contact(key: str, reason: str) -> None`；`unblock_contact(key: str) -> None`。
- `materialize_due(now: float) -> List[int]` 只建立到期序号的唯一 job；`on_confirmed(job_id: int, confirmed_at: float) -> None` 原子完成本次并计算下一次；第 3 次置 completed，不再产生任务。首次创建立即到期，后续 `not_before = confirmed_at + 7200`。
- `ClockGuard(store, wall=time.time, monotonic=time.monotonic)`；`now() -> float`、`trusted() -> bool`、`resume() -> None`。进程内 wall 与 monotonic 的增量差绝对值超过 5 秒即标为不可信；跟进暂停到显式恢复并重新建立 7200 秒保守窗口。重启后已存在跟进也至少等待一个新的 7200 秒窗口，防止无法判断离线调钟而提前发送；首次任务可在显式恢复、会话核对后立即到期。
- 请求 ID 绑定规范化目标和指令的摘要；同 ID 不同内容返回 `request_id_conflict`。新 ID 遇到有效轮次只返回 existing；明确 cancel 后才可新建。每轮发送序号来自数据库唯一约束，不以内存次数为准。

- [x] **Step 1: 写失败测试。** 采用 FakeClock 验证：
  ```python
  self.assertEqual(next_due - confirmed_at, 7200)
  self.assertEqual(len(submitted_sequences), 3)
  self.assertEqual(submitted_sequences, [1, 2, 3])
  self.assertEqual(materialize_at_7199, [])
  ```
  另测重复请求、不同内容复用 ID、不同目标各算三次、跨日无自动新轮、收到回复/人工发送/移出名单取消、未来任务不占队头、重启/调钟不补发、停止状态不被手动 start 清掉。
- [x] **Step 2: RED。** discover `test_campaigns.py`。
- [x] **Step 3: 实现 CampaignManager 与 ClockGuard。** 用户指令上限 4000 字/16 KiB，请求 ID 上限 128 字；目标采用 Task 3 已解析集合。不新增后台独立线程操作手机。
- [x] **Step 4: GREEN。** 运行本任务与 `test_scope.py`、`test_store.py`；用虚拟时钟，不实际等待两小时。
- [x] **Step 5: 提交。** `git commit -m "feat: schedule manual three-message outreach rounds"`。

### Task 5: 三类生成任务共用风格与有界上下文

**Files:** Create `generation.py`, `tests/test_generation.py`; Modify `provider.py`, `tests/test_provider.py`。

**Interfaces:**
- `GenerationRequest(kind: str, messages: Tuple[Message,...], new_messages: Tuple[Message,...]=(), campaign_id: Optional[int]=None, sequence: Optional[int]=None, sent_in_round: Tuple[str,...]=(), instruction: str='')`；kind 为 reply/proactive_initial/proactive_followup。
- `GenerationResult(disposition: str, reply: Optional[str]=None, reason: str='')`；disposition 为 reply/stop_contact/needs_attention。
- `build_task_context(request: GenerationRequest, limits: Config) -> dict`：reply 继续调用现有 build_context；主动任务必须有有效轮次和 1..3 序号，允许上下文为空，不制造 incoming。总上下文 20 条/8000 字符，轮次已发内容不被静默丢掉；无法满足上限返回明确失败。
- `ReplyProvider.generate_task(request, persona='', limits=None, cancel=None) -> GenerationResult`；旧 `.generate` 签名和字符串结果保留为兼容包装，旧测试继续适用。Codex 输出 schema 明确区分三种 disposition，只有 reply 可以带可发送正文；FakeProvider 提供确定性离线实现。

- [x] **Step 1: 写失败测试。** `test_proactive_empty_context_is_not_fake_incoming` 断言上下文没有捏造 user 消息；`test_all_task_types_receive_same_persona` 检查 prompt 中风格一致；跟进包含已发原文、sequence 与任务说明。测试 stop_contact 不产生正文、模糊拒绝返回 needs_attention、非法结果不成为草稿、跨会话内容不混入、工具仍全部禁用。
- [x] **Step 2: RED。** discover `test_generation.py`，确认接口缺失；旧 provider 测试作为基线。
- [x] **Step 3: 实现请求构建和 Codex schema。** 不改变已有进程取消/超时/输出大小保护；聊天文本与风格/任务指令分别编码，聊天不能变更策略或发起其他任务。对模糊拒绝不擅自永久封禁。
- [x] **Step 4: GREEN。** 运行 `test_generation.py`、`test_provider.py`；只使用 runner stub，不请求真实模型或手机。
- [x] **Step 5: 提交。** `git commit -m "feat: generate contextual openers and follow-ups with shared persona"`。

### Task 6: 主动发送复核与轮次检查点

**Files:** Modify `sending.py`, `store.py`, `tests/test_sending.py`; Create `tests/test_campaign_sending.py`。

**Interfaces:**
- `Sender(..., *, scope=None, campaigns=None, clock=time.time)` 可选依赖保留旧调用；所有任务 `_valid` 使用同一 `head_job(now)`、最新范围、服务代次、会话版本和取消状态。
- `Store.mark_sending` 对主动 job 在同一事务核对轮次有效、序号 1..3 且未提交，然后记录唯一提交凭证。`finish_attempt` 保持 sent/uncertain 语义；主动 sent 调用 `CampaignManager.on_confirmed` 必须处于同一业务事务，崩溃不会丢下一次或重复推进。
- `Sender.send`/`.reconcile` 返回现有 SendResult；新输入或人工发送交由 scheduler/campaigns 更新，不抢先吞掉输入。运行时绑定适用于所有 App，微信校准校验仍在微信适配器。

- [x] **Step 1: 写失败测试。** 模拟 `input_text` 后新消息、名单变更、跨日、暂停、错误 App、取消轮次，断言 submit 次数为 0；模拟 submit 后崩溃，断言同序号提交次数始终为 1。`test_confirm_and_next_due_commit_atomically` 在落盘边界注入故障再恢复，下一序号最多一个且时间至少 +7200。
- [x] **Step 2: RED。** discover `test_campaign_sending.py`。
- [x] **Step 3: 扩展 Sender 与检查点。** 输入前与提交前都核对策略；不清除用户草稿。未知结果占序号、阻止该会话后续，恢复只读核对；已提交动作不因之后取消而声称撤回。
- [x] **Step 4: GREEN。** 运行 `test_campaign_sending.py`、`test_sending.py`、`test_store.py`、`test_campaigns.py`。
- [x] **Step 5: 提交。** `git commit -m "feat: checkpoint and revalidate proactive sends"`。

### Task 7: 将服务接入统一调度与新命令

**Files:** Modify `scheduler.py`, `service.py`, `control.py`, `tests/test_scheduler.py`, `tests/test_service.py`, `tests/test_cli.py`; Create `tests/test_multi_app_service.py`。

**Interfaces:**
- `Scheduler(store, allowed=None, *, scope=None, campaigns=None)`：微信兼容旧 allowed；非微信用 ScopePolicy，没有逐联系人校准过滤，但仍要求打开后的接收对象绑定。未知初始输入边界用 candidate unread_count 或 snapshot.initial_unread 的真实证据；两者皆无就记录该会话 ambiguous，不把历史全部视为新输入。
- `Service` 接受 AppAdapter；preview token 由适配器接口提供，禁止直接取微信 calibration。所有生成通过 generate_task；保留最多 2 workers 和已有晚到结果作废机制。
- 控制命令：`discover(full: bool)`、`scope_get`、`scope_set(date, selected)`、`scope_delete(date)`、`campaign_start(request_id, snapshot_id, selected=None, instruction='')`、`campaign_list`、`campaign_cancel(campaign_id)`、`contact_unblock(conversation)`；原命令保留。新增字段做严格类型/长度和当前 App 校验。
- status 增加 `app_id/adapter_verified/timezone/daily_scope/coverage/discovery_id/campaigns/clock_trusted`；保留旧字段。序号、确认次数、下一次到期、跳过原因均可见。
- 手机操作继续只在服务主线程；控制 socket 请求体默认仍限制 4096 bytes，超长名单返回明确错误，不静默截断；大范围使用已保存 discovery ID 而非传整个列表。已有回复优先，到期主动任务才参与生成/发送。

- [x] **Step 1: 写失败集成测试。** 非微信无每日名单的多个会话可以处理；微信未允许的会话永不读取；新回复原子取消等待/生成/已输入未提交的跟进；其他人的未读不终止本人的轮次。另测 partial/stale discovery 启动错误、初始无分界不捏造输入、AI 乱序与优先级、未到期不阻塞、停止联系与解除、socket 变更名单在当前事务边界生效。
- [x] **Step 2: RED。** discover `test_multi_app_service.py`。
- [x] **Step 3: 接线，不增加第二个循环。** 共享 InstanceLease；两个不同 state_dir 的真实服务仍竞争同一 Runtime lease。策略变更主动取消受影响轮次，并使进行中的旧 draft/version 失效。预览不推进次数、不提交；非微信适配器 token 未验证时禁止 --send。
- [x] **Step 4: GREEN。** 运行本任务与 `test_service.py`、`test_scheduler.py`、`test_cli.py`、`test_phone.py`。需要 Unix socket/子进程时按环境权限执行；不能修改断言来绕过沙箱错误。
- [x] **Step 5: 提交。** `git commit -m "feat: unify reactive replies and manual outreach scheduling"`。

### Task 8: CLI、离线演示与用户文档

**Files:** Modify `cli.py`, `config.example.json`, `demo.py`, `README.md`, `README.en.md`, `CHANGELOG.md`, `docs/CALIBRATION.md`, `docs/VALIDATION.md`; Create `docs/MULTI_APP.md`, `tests/test_multi_app_cli.py`。

**Interfaces:**
- 配置 `app_id` 选择活动 App，缺失按 wechat；新命令准确映射 Task 7，不在 CLI 再写策略。
- `discover --full`；`scope show`、`scope set --date YYYY-MM-DD --contacts ID...`（无 ID 表示显式空清单）、`scope clear --date YYYY-MM-DD`（删除当天记录恢复默认）；微信拒绝 set/clear。
- `campaign start --request-id ID --snapshot-id ID [--contacts ID...] [--instruction TEXT]`；省略 contacts 为本快照全量；显式空目标返回 no_targets。`campaign list`、`campaign cancel CAMPAIGN_ID`；`contact unblock ID`。
- 所有命令仍使用同一配置路径和私有 socket。帮助和 JSON 明确区分接受任务、生成草稿、发送确认；`demo --scenario multi-app` 展示非微信默认全部、每日子集和虚拟时间的三次跟进，不访问手机/Codex。

- [x] **Step 1: 写失败 CLI 测试。** 缺 app_id 保持微信；每日空/删除语义不同；超长/非法日期/未知 App/伪造快照/重复 request_id 返回明确状态；未验证 qingteng/jimu 启动真实发送失败且无手机动作。演示断言：`ok is True`、未回复序号 `[1,2,3]`、第 4 次不存在、reply 取消案例无后续 submit。
- [x] **Step 2: RED。** discover `test_multi_app_cli.py`。
- [x] **Step 3: 实现命令与文档。** 清楚标注重启后跟进采用新的 2 小时保守等待、候选快照 300 秒期限、范围未设置/显式空的差异；不称未验收适配器为可用。不把真实风格文档复制到例子。
- [x] **Step 4: GREEN。** 运行本任务与 `test_cli.py`；执行离线 multi-app demo，输出 ok:true。
- [x] **Step 5: 提交。** `git commit -m "feat: expose daily scopes and manual chat rounds in CLI"`。

### Task 9: 通用引擎验收与适配器交接

**Files:** Modify `docs/VALIDATION.md`, `docs/REVIEW.md`, `.github/workflows/ci.yml`（仅需增加新 demo 时）。

- [x] **Step 1: 执行完整离线验收。** `PYTHONPATH=src python3 -m unittest discover -s tests -v`；应全部通过，保留原 68 项的行为覆盖；再执行原 demo 和 multi-app demo。编译 `python3 -m compileall -q src`（将 PYTHONPYCACHEPREFIX 指向临时私有目录），`git diff --check` 与 `python3 scripts/check_release.py --history` 无发现。
- [x] **Step 2: 从干净源码快照再验证。** 使用 `git archive HEAD` 到临时目录，仅从源码运行测试与两个 demo，不带 config.json、校准、截图、业务数据库或登录资料。
- [x] **Step 3: 更新验证记录。** 记录实际命令、结果、未验收 App 与已知限制；未进行两小时真机无人值守验收时明确说明，虚拟时钟不等于真机验收。
- [x] **Step 4: 独立最终代码评审。** 按 requesting-code-review 与用户模型路由，使用新的 gpt-5.6-sol、xhigh 评审本实施分支；修复重要问题后仅跑影响范围测试，最后执行必要整体验收。不得把设计 builder check 当代码评审。
- [x] **Step 5: 提交验收记录并进入适配计划。** `git commit -m "docs: record multi-app engine verification and remaining device gates"`。只有确认真实适配可用后，才能更新相应 App 的支持状态。

## 自查与执行边界

- 覆盖关系：Spec 5→Tasks 1/5/6/7；Spec 6→Task 3；Spec 7→Task 4/6/7；Spec 8→Task 2；Spec 9→Task 7/8；Spec 10→Task 9 与两个适配计划。
- 同一接口只在其所属 Task 定义；后续任务使用该定义。真实适配器使用 base 契约，不另造发送循环。
- core 可离线完成；真实 App 操作先按 iphone-use 技能检查服务，再只读勘察；真实发送必须有明确指定测试对象。没有设备时不阻塞 core，也不能把模拟适配器冒充上线适配器。
- 计划中的 300 秒快照期限、5 秒调钟检测、重启后保守两小时窗口、指令和 socket 上限是实施者的明确选择，用于落实设计中的过期与计时保护，不改变用户指定的每轮 3 次和跟进最小间隔。
- 设计已确认；本计划已获用户批准，仅执行通用引擎 Tasks 1–9；两个真实 App 的界面接入延后。按用户已选的本会话连续实施、最终独立评审方式执行，实施工作在受管理的隔离 worktree 内进行。

## 实际验收补充

通用引擎 Tasks 1–9 已完成；用户明确将青藤之恋与积目真实 UI 接入延期。独立评审发现的 7 项问题已在 f2ac04e 中以 RED→GREEN 修复，完整测试和干净源码复验均为 130/130。为轮次历史保留期增加 schema v2 时间戳迁移；快照绑定策略版本，初始主动读取需要可靠边界，人工命令与未提交草稿均保留恢复语义。最终实施裁决、代价和 1 项暂缓 Minor 见 docs/REVIEW.md。本轮没有手机或真实模型生成操作，没有更新公开仓库。
