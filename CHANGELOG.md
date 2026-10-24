# Changelog

## 0.2.0 — 2026-10-24 · experimental

- 发布通用引擎与微信流程修复，保留现有微信配置、允许名单、校准和预览要求。
- 未读先持久入队再进入聊天；无未读且无既有任务的允许会话不会被打开。
- 使用列表免打扰标记与唯一行／标题绑定，常规回复不再反复进入头像和详情。
- 明确配置的群聊接入同一自动队列，保留发言者和非文字类型。
- 识别中英文“发送”／`send`，不限制输入法；使用语义元素点击修复第三方键盘相对坐标问题。
- 短暂“正在发送”最多增加两次只读核对；确认发送还需输入框为空，绝不重复提交。
- 兼容“最常使用”搜索返回页；扩大大列表 HTTP 与多页事务的有界读取预算。
- 长目录使用私有短 socket 路径；重复控制服务启动失败不会删除现有服务的 socket。
- 更新中文／英文 README、校准说明和验证边界。新代码经过合成回归；先前辅助真机发送不等同于修复版本的无人值守验收。

### 通用引擎与主动聊天

- 在原引擎上增加 App 适配契约和按 App 隔离的状态；旧配置仍默认微信，原校准/名单规则保留。
- 增加非微信每日可选名单、候选覆盖与过期检查。
- 手动主动轮次与未读回复共用队列；每个目标最多三次、未回复间隔两小时、回复后取消，复用风格文档。
- 增加候选、每日名单和轮次 CLI，以及无需设备/登录的多 App 合成回放。
- 增加 Soul 配置标识与独立状态范围，沿用非微信每日名单和主动轮次规则。
- 青藤之恋、积目和 Soul 的真实 UI 尚未接入，禁用真实运行；未进行新的真机发送。

### 发布验证

- 干净源码导出包含全部 74 个跟踪文件；隔离目录中的 157 项离线测试、单轮运行、两个演示与编译通过。
- 源码、导出包及公开仓库历史完成脱敏检查；真实聊天、截图、联系人映射和私密配置不随源码发布。
- 0.2.0 保持实验状态；修复版本的长时间真机无人值守验收仍待完成。

## 0.1.0 — Unreleased / experimental

- Bottom-first queue with persisted conversation versions; muted or unknown conversations excluded.
- Contextual replies through a separately logged-in Codex CLI, without another API key.
- Shared iPhone Use device transactions, pre-send verification and durable uncertain-send checkpoints.
- Preview default, explicit contact allowlist, local pause/resume/stop controls and offline replay.
- Synthetic tests, privacy checks, Chinese/English documentation and source-release preparation.

Real-device evidence covers a File Transfer Assistant self-test, not a completed two-contact acceptance test. Contact calibration still requires developer assistance. See [validation](docs/VALIDATION.md).
