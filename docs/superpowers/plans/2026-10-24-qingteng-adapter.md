# Qingteng Chat Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将青藤之恋已有聊天列表接入通用引擎，并以真实设备证据决定是否开放发送。

**Architecture:** 实现 AppAdapter，不复制策略、主动计时或 Sender。先只读勘察，再用脱敏合成布局测试解析与导航，最后独立验收发送能力。

**Tech Stack:** Python 3.9+、unittest、iPhone Use 0.3.8、通用引擎契约。

**Spec:** `docs/superpowers/specs/2026-10-24-multi-app-chat-engine-design.md`；依赖 `2026-10-24-multi-app-chat-engine.md` Tasks 1–9。

## Global Constraints

- app_id=`qingteng`；只处理已有聊天列表，不进入推荐/匹配页，不采集主页信息。
- 当天未指定清单为全部已有会话；不要求逐联系人人工身份校准；仍需运行时接收对象绑定、免打扰状态与唯一发送结果证据。
- 继承主计划全部发送次数、两小时间隔、预览、范围、模型与隐私约束；未验证适配器禁止真实发送。
- 当前没有真实页面材料和指定测试联系人；不得猜测控件、坐标或声称已验收。

## Review Focus

- 同名会话重排后不能打开另一人：绑定证据与重新定位测试（Task 2）。
- 顶部置顶/提示卡混在会话列表中不能生成虚假联系人：解析测试（Task 2）。
- 未读只有红点时不能把历史当新消息：分界能力测试（Task 2）。
- 弹窗、会员提示、键盘遮挡不能改变发送对象或误点付费：导航拒绝未知页面（Task 2）。
- 新旧相同正文、发送失败图标或回显延迟不能证明新消息已成功发送：结果验证测试（Task 3）。

---

### Task 1: 只读勘察与可实现能力记录

**Files:** Create `docs/adapters/QINGTENG.md`, `tests/fixtures/qingteng_layouts.py`。

**Interfaces:** 合成夹具提供 `LIST_TOP/LIST_END/CHAT_LATEST/CHAT_WITH_DRAFT/AMBIGUOUS_CONTACT/MUTED_CONTACT/UNKNOWN_PAGE`，类型为 iPhone Use observation dict，内容全部虚构。只有真机观察到的布局结构才能作为已支持版本证据。

- [ ] **Step 1: 检查设备与只读范围。** 使用 iphone-use/setup 技能取得 READY；在用户可提供手机时观察聊天列表、滚动、已有聊天和返回，不能输入/发送、标记喜欢、匹配或开会员。无设备时只记录未勘察状态，不能编造夹具。
- [ ] **Step 2: 记录能力。** 在 QINGTENG.md 记录 App/语言/设备布局版本、已有会话入口、列表完整性、未读分界、免打扰、对象绑定、草稿与发送证据来源；不写个人资料、设备标识、实际聊天或绝对私有路径。没有可靠证据的能力明确记 unavailable。
- [ ] **Step 3: 写合成观察夹具并审查脱敏。** 只保留必要控件结构，虚构所有名称、消息、时间和标识；原始截图与树保留于忽略的私有目录。
- [ ] **Step 4: 检查文档/夹具并提交。** `git diff --check` 和发布检查无发现；`git commit -m "docs: record Qingteng adapter observations without personal data"`。无设备时提交能力未验证说明即可，不继续凭空完成后续真实适配。

### Task 2: 解析与导航适配

**Files:** Create `adapters/qingteng.py`, `tests/test_qingteng_adapter.py`; Modify `adapters/registry.py`。

**Interfaces:** `QingtengAdapter(bridge, profile)` 实现 base.AppAdapter；`parse_list(view, profile) -> Tuple[ConversationCandidate,...]`、`parse_chat(view, key, profile) -> ChatSnapshot` 为纯函数。`profile` 仅保存已观察布局版本与能力证据，非逐联系人校准名单。registry 选择 qingteng；未验证 profile 仅允许只读或模拟，--send 返回 adapter_unverified。

- [ ] **Step 1: 写失败契约测试。** 使用 Task 1 夹具，测试列表顺序、完整分页、免打扰、方向/非文字、保留人工草稿、重复名称歧义、未知卡片/弹窗/会员页面拒绝导航、红点无分界时 initial_unread=None；缺人工联系人档案不自动过滤候选。
- [ ] **Step 2: RED。** `PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_qingteng_adapter.py' -v`，因缺适配器失败。
- [ ] **Step 3: 实现观察/导航。** 遵循勘察证据，不依赖微信坐标；所有动作运行于 PhoneBridge.transaction。仅清除可证明为本服务写入且未变化的草稿。
- [ ] **Step 4: GREEN。** 运行本任务与 test_adapters.py、test_scope.py、test_multi_app_service.py；共享契约测试对 qingteng 运行。
- [ ] **Step 5: 提交。** `git commit -m "feat: add observed Qingteng chat navigation adapter"`。

### Task 3: 发送证据和真机验收

**Files:** Modify `adapters/qingteng.py`, `tests/test_qingteng_adapter.py`, `docs/adapters/QINGTENG.md`, `docs/VALIDATION.md`。

**Interfaces:** Qingteng session 实现 Sender 所需输入、草稿核对、单次 submit 和唯一新增己方消息核对；能力 verified token 仅在页面与发送证据验收后生成，不能用硬编码 True 绕过。

- [ ] **Step 1: 写失败发送测试。** 模拟新旧同文气泡、失败标记、延迟回显、取消/新消息/错页面，分别断言 sent/uncertain/stale 和 submit<=1。
- [ ] **Step 2: RED→GREEN。** 运行本文件测试，最小实现后连同 test_campaign_sending.py、test_sending.py 验证；不在 App 适配器另写两小时定时器。
- [ ] **Step 3: 先真机预览。** 读取既有会话和当前范围，核对对象、方向、返回和候选覆盖；预览不发送，不推进轮次。若关键能力不足，记录缺口并保持真实发送禁用。
- [ ] **Step 4: 在用户明确指定测试对象后进行单次真实发送验收。** 当前计划不推定授权。用该对象验证唯一回显、返回列表与停止，不向其他联系人试发；多轮调度用合成时钟验证，不向真人连续缩短间隔。
- [ ] **Step 5: 更新能力记录并提交。** 仅完成的版本标为 verified；`git commit -m "test: record Qingteng send verification and capability limits"`。未获测试对象或设备时如实交付未验证状态，不宣称可无人值守。
