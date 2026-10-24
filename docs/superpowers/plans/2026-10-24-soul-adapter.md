# Soul Chat Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将 Soul 已有聊天列表接入通用引擎，提供经验证的读取、发送与主动跟进能力。

**Architecture:** 使用现有 AppAdapter 契约；每日范围、主动轮次、生成、发送记录和任务队列由通用引擎管理。Soul 只实现实际页面导航、解析与发送证据。

**Tech Stack:** Python 3.9+、unittest、iPhone Use 0.3.8。

**Spec:** `docs/superpowers/specs/2026-10-24-multi-app-chat-engine-design.md`。

## 当前状态与约束

- 已加入 `app_id=soul` 配置和状态范围；真实运行返回 `adapter_not_available`，不连接手机。
- 以下界面任务尚未实施，等待用户之后接入手机。不得猜测 Soul 的控件、包标识、未读或免打扰能力。
- 仅访问已有聊天列表；不进入推荐、匹配、主页采集或新增关系流程。
- 非微信每日规则适用：当天未设清单为全部已有会话，显式空清单为无人，删除清单恢复默认；无需用户逐人建立人工校准名单，仍需运行时核对实际对象。
- 免打扰或免打扰状态未知的会话排除；名单外未读不阻塞其他可处理会话。
- 主动轮次手动开始，每个目标最多 3 次含首次，未回复间隔至少 7200 秒；回复后取消剩余跟进，沿用风格文档和统一队列。

### Task 1: 只读勘察与合成材料

**Files:** Create `docs/adapters/SOUL.md`, `tests/fixtures/soul_layouts.py`。

- [ ] 按 iphone-use/setup 技能检查 READY；只读观察已有列表、分页、聊天和返回，不输入或发送。
- [ ] 记录实际版本、列表末尾证据、系统或活动卡区分、免打扰、消息方向、未读分界、草稿、对象绑定和发送反馈能力。能力缺失时明确记录，不构造假证据。
- [ ] 建立脱敏合成夹具：列表首尾、最新聊天、有草稿、同名歧义、免打扰及未知页面；不提交真实截图、设备标识或私人聊天。
- [ ] 校验资料并提交；无法可靠观察必要能力时保持真实运行禁用。

### Task 2: 解析、定位与预览

**Files:** Create `src/wechat_autoreply/adapters/soul.py`, `tests/test_soul_adapter.py`; Modify `src/wechat_autoreply/adapters/registry.py`。

**Interfaces:** `SoulAdapter(bridge, profile)` 实现 `AppAdapter`；profile 仅描述已观察版本与布局，不包含逐人允许或人工校准名单。

- [ ] 先写失败契约测试：排序与覆盖、非个人会话排除、免打扰、同名歧义、消息方向、非文字输入、未知未读分界、草稿保护和未知弹窗。
- [ ] 运行测试确认 RED，再按实际观察实现解析和事务式导航；不套用其他 App 的坐标或选择器。
- [ ] 核对运行时对象绑定与可靠输入边界；只在证实列表末尾后声明完整覆盖，歧义时不发送。
- [ ] 运行 Soul 测试及 `test_adapters.py`、`test_scope.py`、`test_multi_app_service.py`，通过后提交；未知布局返回 `adapter_unverified`。

### Task 3: 发送与设备验收

**Files:** Modify `src/wechat_autoreply/adapters/soul.py`, `tests/test_soul_adapter.py`, `docs/adapters/SOUL.md`, `docs/VALIDATION.md`。

- [ ] 先写失败测试覆盖重复正文历史气泡、失败标记、回显延迟、提交中断、新输入和取消；不确定发送不得自动重发。
- [ ] RED→GREEN 实现统一 Sender 契约：输入、精确核对并清理本服务草稿、单次提交、唯一新增己方气泡核对、返回列表；不另建队列或计时进程。
- [ ] 运行 Soul 测试及 `test_campaign_sending.py`、`test_sending.py`、`test_campaigns.py`。
- [ ] 真机只读与预览验收后，由用户指定 Soul 测试对象再验证单次发送；微信文件传输助手授权不适用于 Soul 联系人。两小时间隔仅用虚拟时钟测试，不向真人缩短间隔批量发送。
- [ ] 记录实际验收版本与限制；未完成设备验收时不得标为无人值守可用，提交脱敏结果。
