# 微信 AI 自动回复服务 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建 Mac 常驻服务，复用 iPhone Use，在微信中从消息列表底部开始处理允许联系人的未读文字消息，用 AI 生成并核对发送，跳过免打扰会话。

**Architecture:** 一个进程、一个手机工作线程、最多两个 AI 请求并行；SQLite 持久化会话版本、队列和发送检查点。微信界面识别与手机事务独立于调度和 AI，真实校准失败时禁用发送并报告证据。

**Tech Stack:** Python 3.9+、标准库 dataclasses/sqlite3/unittest/subprocess/concurrent.futures/socket、已安装 iPhone Use 0.3.8 的 Runtime。首版不增加 pip 依赖。

**Spec:** `docs/superpowers/specs/2026-10-23-wechat-ai-autoreply-design.md`

## Global Constraints

- 自动发送只对配置中明确指定的联系人启用；初始名单为空。
- 首版处理文字单聊。群聊、公众号、服务通知、图片、语音、视频和文件不自动回复。
- 开启静音或消息免打扰的群和个人均不进入回复队列；状态无法确认时跳过。
- 从消息列表最下面的未读会话开始，逐个向上回复，最新的最后回复。
- AI 并发默认 2，手机并发始终 1。
- 消息列表本地轮询间隔初值为 2 秒，可配置。
- 最近 20 条已确认方向的文字消息、总计约 8000 字符；默认回复不超过 300 字。
- 预览模式绝不发送；自动模式不逐条请求批准。仅文件传输助手获用户授权做真机发送测试，普通联系人名单为空。
- 不修改插件缓存、不绕过共享 operation.lock、不自动重放不确定发送。

## Review Focus

- 免打扰标记缺失或识别失败：不能把 unknown 当 false；Task 1/4 验证。
- 另一客户端在核对与发送间切换会话：整个发送事务共享同一锁；Task 4 验证。
- 两条完全相同的消息及列表跨页重排：保留重复次数、不能伪造顺序；Task 1/2 验证。
- AI 在暂停后返回或后面的回复先完成：旧代次失效且不得越序发送；Task 3/5 验证。
- 发送已生效但记录尚未落库时崩溃：重启只核对，不再次发送；Task 4/5 验证。

## File Structure

- `src/wechat_autoreply/models.py`：标准观察、候选、消息和任务数据对象。
- `src/wechat_autoreply/config.py`：严格 JSON 配置与路径/数值校验。
- `src/wechat_autoreply/wechat.py`：基于已校准控件的页面、会话与免打扰识别。
- `src/wechat_autoreply/phone.py`：插件发现、共享 Runtime、只读校准与手机事务。
- `src/wechat_autoreply/store.py`：SQLite、私有文件和持久化队列状态。
- `src/wechat_autoreply/scheduler.py`：列表逆序、版本、重叠匹配和公平处理。
- `src/wechat_autoreply/provider.py`：隔离上下文、Codex CLI 与离线实现。
- `src/wechat_autoreply/sending.py`：发送前复核、草稿核对和不确定结果处理。
- `src/wechat_autoreply/service.py`：检测/生成/发送循环及生命周期。
- `src/wechat_autoreply/control.py`：私有本地控制通道和即时暂停事件。
- `src/wechat_autoreply/cli.py`、`__main__.py`：中文状态与操作入口。
- `tests/test_*.py`、`tests/fixtures/`：风险回放、契约与脱敏样本。
- `config.example.json`、`persona.example.txt`、`启动微信助手.command`、`README.md`：配置和使用说明。

### Task 1: 只读微信校准与标准观察

**Files:** Create `models.py`, `config.py`, `wechat.py`, `phone.py`, package entry, `tests/test_wechat.py`, `tests/test_config.py`, `tests/fixtures/`，以及支持这些测试的最小包元数据。

**Interfaces:**
- Produces `Message(direction, text, kind, anchor)`, `ConversationCandidate(key, label, position, unread, muted, signature)`, `ChatSnapshot(key, messages, muted, draft, at_latest, identity_verified)`。
- Produces `parse_list(observation, calibration) -> list[ConversationCandidate]`, `parse_chat(observation, calibration) -> ChatSnapshot`；`muted` 为 Optional[bool]。
- Produces `PhoneBridge.ready()`, `observe()`, `calibrate()` 和私有 `transaction(callback)`，供 Task 4 使用；依赖仅注入，不在导入时操作手机。

- [x] **Step 1:** 用现有 PUA setup status/READY 复用健康连接，只读检查当前微信；界面动作失败先观察，不能重放不确定动作。记录脱敏结构与未覆盖能力；设备阻塞不影响其余离线任务。
- [x] **Step 2:** 先写测试：未读候选保留列表位置；muted=true/unknown 不可排队；群聊排除；控件截断拒绝完整性结论；重复姓名不可验证；识别消息方向/非文字；配置未知键、路径和越界值拒绝。
- [x] **Step 3:** 运行 `PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_wechat.py' -v`，确认因缺少实现失败；配置测试同理。
- [x] **Step 4:** 实现上述对象与校准驱动解析，默认不认为未知免打扰是关闭。不把个人截图/内容放入 Git；夹具只保留脱敏可观测结构。共享 Runtime 的版本和能力明确验证，不写死缓存版本路径。
- [x] **Step 5:** 重跑微信与配置测试，预期 PASS；真机身份/未读/方向证据不足时明确禁止自动发送并记录待完成事项。
- [x] **Step 6:** 提交 `feat: add calibrated WeChat observations and configuration`。

### Task 2: 可恢复队列与会话版本

**Files:** Create `store.py`, `scheduler.py`, `tests/test_scheduler.py`, `tests/test_store.py`。

**Interfaces:**
- Consumes Task 1 对象。
- Produces `Store(path)`, `enqueue_scan(candidates)`, `observe_chat(snapshot)`, `head_job()`, `set_draft(job_id, version, generation, text)`, `mark_sending(...)`, `finish_attempt(...)`。
- Produces `align_messages(previous, current) -> Alignment`，歧义不能生成新可发送版本；`Scheduler(store, allowed)` 按持久化队列号管理任务。

- [x] **Step 1:** 写测试：列表 A/B/C 按 C/B/A 排队；新输入移队尾；重复扫描不重排；读后红点消失仍保留任务；完全相同消息保留两次；无唯一重叠转 ambiguous；重启 sending 转 uncertain；跨会话上下文隔离；私有目录/数据库权限。
- [x] **Step 2:** 运行 `PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_scheduler.py' -v` 和 store 测试，确认缺少实现导致失败。
- [x] **Step 3:** 实现 SQLite 事务与有序重叠匹配；队列快照和页面覆盖单独保存，无法对齐的跨页扫描不承诺整体顺序。failed/ambiguous/uncertain 移出活动队列但保留可见状态。
- [x] **Step 4:** 重跑 Task 2 测试，预期 PASS；再跑 Task 1 受影响的对象/解析测试。
- [x] **Step 5:** 提交 `feat: persist versioned bottom-first reply queue`。

### Task 3: 上下文隔离与 AI 生成

**Files:** Create `provider.py`, `tests/test_provider.py`, `persona.example.txt`。

**Interfaces:**
- Consumes Task 1 Message 和 Task 2 任务版本。
- Produces `ReplyProvider.generate(messages, persona, limits) -> str`、`CodexProvider(config, runner)`、`FakeProvider()`、`build_context(...)`；runner 可注入且有界。

- [x] **Step 1:** 写测试：20 条/8000 字符限制；新增输入不得静默截断；非文字转人工；Codex 标准输入、临时会话、JSON schema 与工具隔离；拒绝空/错误 JSON/超长输出；换行转单段；取消/超时；无需 API key 的离线合同；不泄漏密钥或其他会话。
- [x] **Step 2:** 运行 `PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_provider.py' -v`，预期因缺少实现 FAIL。
- [x] **Step 3:** 依据已核对官方文档与本机 CLI 帮助实现 Codex 进程调用与本地上下文，复用已有登录、限制体积和超时；禁用生成进程的手机/MCP/执行工具，取消时终止进程组。不读取认证令牌，模型名不硬编码。
- [x] **Step 4:** 重跑 provider 测试，预期 PASS；使用注入 runner 做合同验证，再用虚构消息实际运行已登录 Codex 验证，不依赖 API key。
- [x] **Step 5:** 提交 `feat: add bounded context-aware AI reply provider`。

### Task 4: 共享锁内发送与结果核对

**Files:** Modify `phone.py`；Create `sending.py`, `tests/test_phone.py`, `tests/test_sending.py`。

**Interfaces:**
- Consumes `PhoneBridge.transaction(callback)`、`ChatSnapshot`、Store 的发送检查点。
- Produces `Sender.send(job, cancellation) -> SendResult`、`Sender.reconcile(attempt) -> SendResult`，结果包含 sent/stale/skipped/uncertain，不把 HTTP 成功当发送成功。

- [x] **Step 1:** 写测试：名单/身份/版本/免打扰复核；生成后开启免打扰取消；已有人工草稿保留；完整输入/续传及回读；send 前写 sending；按钮只点一次；发送后 timeout 不重发；相同己方气泡不构成唯一证据；另一个 Runtime 无法插入关键事务；暂停后不发送。
- [x] **Step 2:** 运行 `PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_sending.py' -v` 和 phone 测试，预期 FAIL。
- [x] **Step 3:** 在不修改插件缓存的受控适配层中复用 Runtime 校验、同一 operation.lock 和 session 生命周期；禁止嵌套取非重入锁。整个核对/输入/发送/结果事务有界；网络生成始终在锁外。
- [x] **Step 4:** 重跑发送/手机契约与故障测试，预期 PASS；证明预览路径没有发送调用。
- [x] **Step 5:** 提交 `feat: guard sends with shared device transactions and checkpoints`。

### Task 5: 常驻服务、控制入口与整体回放

**Files:** Create `service.py`, `control.py`, `cli.py`, `__main__.py`, `tests/test_service.py`, `tests/test_cli.py`, `config.example.json`, `启动微信助手.command`, `README.md`。

**Interfaces:**
- Consumes Task 1–4 的 PhoneBridge、Scheduler、Store、ReplyProvider、Sender。
- Produces `Service.tick()`, `run()`, `pause()`, `resume()`, `stop()` 与 CLI run/status/pause/resume/stop/calibrate/resolve/retry/rebaseline/cleanup/demo。

- [x] **Step 1:** 写测试：2 秒检测/2 秒安静期/8 秒最长等待；两个 AI 并行但后面的先完成不能越序；暂停/停止立即使旧结果失效；重启预览；队首失败放行；读后继续跟踪；免打扰不调用 AI；resolve 只更新记录；uncertain 不能 retry；私有控制 socket、大小限制、第二进程不能抢设备。
- [x] **Step 2:** 运行 `PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_service.py' -v` 和 CLI 测试，预期 FAIL。
- [x] **Step 3:** 实现单手机工作线程、生成 futures、私有 AF_UNIX 控制、运行代次和有界退出；控制线程只提交命令/取消事件，不并发操作手机。提供清理与中文状态。
- [x] **Step 4:** 运行 `PYTHONPATH=src python3 -m unittest discover -s tests -v`，预期全绿；运行 CLI demo 验证底到顶、免打扰排除和乱序生成。以临时私有状态目录实测 run/status/pause/resume/stop。
- [x] **Step 5:** 写中文说明、配置和启动入口。Codex 登录或普通联系人身份校准缺失时列出准确未完成项；不把离线 demo 称为微信发送验收。
- [x] **Step 6:** 提交 `feat: run controllable WeChat reply service with replay demo`。

### Task 6: GitHub 开源准备（用户追加）

**Files:** LICENSE、THIRD_PARTY_NOTICES.md、README.en.md、CONTRIBUTING.md、SECURITY.md、CHANGELOG.md、docs/CALIBRATION.md、docs/RELEASING.md、docs/VALIDATION.md、GitHub CI 与脱敏发布检查脚本。

- [x] **Step 1:** 明确这是依赖 iPhone Use 的实验版配套服务，暂未包装为独立可安装插件；中英文说明真实能力、限制与本机配置。
- [x] **Step 2:** 使用 MIT 作为本项目默认许可证，保留上游 MIT 版权；不打包依赖代码、签名材料或个人数据。
- [x] **Step 3:** 先写失败测试再实现跟踪文件和 Git 历史发布检查；检查本机路径、凭据、聊天状态、截图及压缩包；报告不输出匹配内容。
- [x] **Step 4:** 增加只用合成数据的 CI、贡献和缺陷模板；只从通过检查的已提交 Git 内容制作发布包，排除工作区私有数据。
- [x] **Step 5:** 实际检查完整历史、发布包与打包后的离线演示，提交准备内容；不自行创建公开仓库、推送或发布。

### Task 7: 独立审查与验收记录

**Files:** Update README 和本计划进度；评审记录放本计划私有工作目录。

- [x] **Step 1:** 执行全套测试、编译检查、CLI 帮助/离线 demo，检查 Git diff 与凭据/个人内容。
- [x] **Step 2:** 分派独立高推理评审读取 design、plan、变更和测试结果，明确检查上述 Review Focus；作者不替代独立审查。
- [x] **Step 3:** 实质问题先写失败测试再修复，运行相关与全套验证；不得通过降低发送边界来让测试变绿。
- [x] **Step 4:** 记录实测的真机能力、尚缺普通联系人身份校准、功能可运行与整体真机验收的区别。没有发送证据不宣称自动回复已投入使用。
- [x] **Step 5:** 提交修复和验收说明，保持可追踪开发分支，不自行推送/合并/发布。

## Self-review

设计各组件、消息状态/排序/免打扰/故障/控制/AI/私有数据均有所属任务；实际校准先行，但外部设备或凭据阻塞不妨碍离线任务。接口在上述任务之间以 models 和 Store 为界；AI callbacks 只交付结果，数据库和手机状态由服务主循环维护。验证不依赖真实好友，发送只在后续明确测试联系人内验收。

## Execution status — 2026-10-23

Tasks 1–7 完成实现、离线验证和开源准备；一次独立评审的 6 项重要问题完成 RED→GREEN 修复，最终 68 项离线测试通过。文件传输助手的一次真实发送已核对；普通联系人身份校准和两个真实会话的完整验收仍待完成，不能据此宣称整体真机验收通过。GitHub 尚未创建或发布，保留开发分支。详见 `docs/REVIEW.md` 和 `docs/VALIDATION.md`。
