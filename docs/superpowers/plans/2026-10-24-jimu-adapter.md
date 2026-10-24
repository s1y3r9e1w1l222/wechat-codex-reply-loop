# Jimu Chat Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将积目已有聊天列表接入通用引擎，提供经验证的读取、发送与主动跟进能力。

**Architecture:** 使用与微信、青藤之恋相同的 AppAdapter 契约，由引擎管理每日范围和主动轮次。积目只负责本 App 的实际页面与发送证据，不复制其他 App 的选择器或已验收结论。

**Tech Stack:** Python 3.9+、unittest、iPhone Use 0.3.8、通用引擎契约。

**Spec:** `docs/superpowers/specs/2026-10-24-multi-app-chat-engine-design.md`；依赖 `2026-10-24-multi-app-chat-engine.md` Tasks 1–9。

## Global Constraints

- app_id=`jimu`；只访问已有聊天列表，不进入推荐、匹配或新建关系流程。
- 非微信每日规则和无人工逐人校准规则适用；实际接收对象、免打扰、草稿和发送结果仍必须核对。
- 主动轮次手动开始，最多 3 次含首次，未回复间隔 7200 秒；沿用风格文档，所有发送都走统一队列。
- 当前没有积目页面观察和指定测试对象；未知能力不得猜测实现，未验收时禁用真实发送。

## Review Focus

- 列表中的系统消息/推荐卡不是已有个人会话：解析与覆盖测试（Task 2）。
- 同名联系人或入口位置重排不能改变目标：绑定与重新定位测试（Task 2）。
- 未读标记/消息方向未知时不能编造上下文：真实分界测试（Task 2）。
- 键盘、弹窗、会员/活动页面不能误操作：仅允许已知页面动作（Task 2）。
- 重复正文、失败图标、连接中断不能导致重发：单次提交与恢复测试（Task 3）。

---

### Task 1: 只读勘察与合成材料

**Files:** Create `docs/adapters/JIMU.md`, `tests/fixtures/jimu_layouts.py`。

**Interfaces:** 合成 observation dict 命名 `LIST_TOP/LIST_END/CHAT_LATEST/CHAT_WITH_DRAFT/AMBIGUOUS_CONTACT/MUTED_CONTACT/UNKNOWN_PAGE`；未知字段不构造假证据。

- [ ] **Step 1: 按 iphone-use/setup 技能检查 READY。** 手机可用时只读观察已有列表、分页、聊天、返回；不进行匹配、点赞、新增联系、输入发送或会员操作。无设备则记录未验证，不虚构 UI。
- [ ] **Step 2: 记录实际版本与能力。** JIMU.md 写明页面入口、完整扫描、未读分界、运行时对象绑定、免打扰、方向/草稿、发送反馈是否可判断；仅记录结构与版本，无私人资料或聊天原文。
- [ ] **Step 3: 根据观察建立合成夹具。** 名称、正文、时间、ID 全部替换为虚构值；真实截图/树仅留在忽略目录。
- [ ] **Step 4: 校验并提交。** 格式与脱敏检查通过后 `git commit -m "docs: record Jimu chat capabilities using synthetic examples"`。能力不具备时明确停止真实适配路径，而不写一个假可用 adapter。

### Task 2: 积目解析、定位与预览

**Files:** Create `adapters/jimu.py`, `tests/test_jimu_adapter.py`; Modify `adapters/registry.py`。

**Interfaces:** `JimuAdapter(bridge, profile)` 实现 base.AppAdapter；`parse_list(view, profile) -> Tuple[ConversationCandidate,...]`、`parse_chat(view, key, profile) -> ChatSnapshot`。profile 为布局和能力材料，无逐联系人允许/校准名单。registry 注册 jimu，未知版本返回 adapter_unverified。

- [ ] **Step 1: 写失败契约测试。** 测排序/完整性、系统卡排除、无人工校准候选保留、免打扰、同名歧义、消息方向/非文字、未读无分界、人工草稿不清除、未知弹窗不点击、输入控件歧义不提交。
- [ ] **Step 2: RED。** `PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_jimu_adapter.py' -v`，接口缺失导致失败。
- [ ] **Step 3: 按已观察界面实现。** 使用 PhoneBridge 的事务和取消机制；保留当前会话绑定链路，不按标题或坐标单独推断唯一身份。完整范围只有证实列表末尾才置 true。
- [ ] **Step 4: GREEN。** 运行本任务和 test_adapters.py、test_scope.py、test_multi_app_service.py；积目通过同一套共享契约。
- [ ] **Step 5: 提交。** `git commit -m "feat: add observed Jimu chat adapter and preview"`。

### Task 3: 发送能力与设备验收

**Files:** Modify `adapters/jimu.py`, `tests/test_jimu_adapter.py`, `docs/adapters/JIMU.md`, `docs/VALIDATION.md`。

**Interfaces:** session 按统一 Sender 契约实现输入、清除本服务草稿、单次 submit、返回列表和唯一新增己方消息验证；不为本 App 另建发送进程或计时状态。

- [ ] **Step 1: 写失败发送测试。** 覆盖相同正文历史气泡、发送失败、回显延迟、提交中断、新输入和取消；断言任何不确定情况不自动补发。
- [ ] **Step 2: RED→GREEN。** 最小实现后运行本任务及 test_campaign_sending.py、test_sending.py、test_campaigns.py。
- [ ] **Step 3: 真机只读/预览验收。** 校验入口、当前对象、消息、免打扰、草稿、返回与覆盖；有任何能力缺口则明确展示并保持 --send 禁用。
- [ ] **Step 4: 用户指定测试对象后验收真实单次发送。** 不把微信文件传输助手授权转用于积目联系人；核对唯一新增气泡和返回列表。两小时间隔用虚拟时钟验证，不能缩短间隔向真人批量测试。
- [ ] **Step 5: 记录验收版本、限制和提交。** `git commit -m "test: verify Jimu submission evidence and supported layout"`；没有真机发送验收时只能交付未验证适配器，不能标为无人值守可用。
